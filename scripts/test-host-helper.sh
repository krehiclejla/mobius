#!/usr/bin/env bash
# Real self-update of the self-hosted replacement helper. An owner installed
# the helper from <previous>; <target>'s image must bring its own worker to
# that host by itself, through the installed launcher and systemd units,
# driven only by requests the app writes into /data.
#
#   sudo scripts/test-host-helper.sh <previous-sha> <target-sha>
#
# Both SHAs must have published official images. Run on a disposable systemd
# host with Docker Compose (a CI runner); it installs root-owned units there.
#
# 1. Deploy <previous> from its own checkout, as an owner does, and set up the
#    owner.
# 2. Install the helper once from that checkout.
# 3. The app requests <target>: the installed worker replaces the container
#    and offers <target>'s worker when its revision is higher.
# 4. The app requests <previous> again: the offered worker performs this real
#    replacement and becomes the active worker.

set -euo pipefail

PREVIOUS="${1:?previous sha}"
TARGET="${2:?target sha}"
IMAGE=ghcr.io/mobius-os/mobius
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
STATUS=/var/lib/mobius-rebuild/status.json
ENV_FILE=$(mktemp /tmp/mobius-host-helper.XXXXXX.env)
SEED=$(mktemp -d /tmp/mobius-seed.XXXXXX)/checkout
export COMPOSE_PROJECT_NAME=mobius

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

fail() {
  echo "host helper: $*" >&2
  systemctl --no-pager status mobius-rebuild.service mobius-rebuild.path >&2 || true
  journalctl --no-pager -u mobius-rebuild.service -n 80 >&2 || true
  cat "$STATUS" >&2 2>/dev/null || true
  docker logs mobius --tail 60 >&2 2>&1 || true
  exit 1
}

field() {  # <json-file> <python expression over d>
  python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"
}

revision_of() {  # <checkout>: the worker revision it declares (0 before revisions)
  sed -n 's/^WORKER_REVISION = \([0-9][0-9]*\)$/\1/p' "$1/scripts/mobius-rebuild-host.py" | grep . || echo 0
}

active_revision() {
  python3 -c 'import json; print(json.load(open("/var/lib/mobius-rebuild/workers.json"))["active"]["revision"])'
}

wait_status() {  # <nonce> <state>: wait until the root status names this request
  for _ in $(seq 1 240); do
    if [[ -f $STATUS ]] && [[ $(field "$STATUS" 'd.get("request_nonce")') == "$1" ]]; then
      local state; state=$(field "$STATUS" 'd.get("state")')
      [[ $state == "$2" ]] && return 0
      case "$state" in failed|rolled_back|needs_recovery|no_change) fail "request ended $state";; esac
    fi
    sleep 5
  done
  fail "request $1 did not reach $2 within 20 minutes"
}

queue() {  # <sha>: write the request exactly as the app does; prints its nonce
  local nonce; nonce=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
  docker exec -u mobius mobius sh -c "
    printf '%s' '{\"version\":2,\"expected_sha\":\"$1\",\"nonce\":\"$nonce\"}' \
      > /data/mobius-rebuild/inbox/.request.tmp &&
    mv /data/mobius-rebuild/inbox/.request.tmp /data/mobius-rebuild/inbox/request.json" \
    || fail "the app could not queue a request"
  echo "$nonce"
}

replaced_with() {  # <sha>: the container runs and serves exactly that release
  [[ $(docker inspect -f '{{.Image}}' mobius) == $(docker image inspect -f '{{.Id}}' "$IMAGE:sha-$1") ]] \
    || fail "the running container is not sha-$1"
  [[ $(docker exec mobius curl -fsS http://127.0.0.1:8000/api/version \
        | python3 -c 'import json, sys; print(json.load(sys.stdin).get("sha"))') == "$1" ]] \
    || fail "the container does not serve sha-$1"
}

echo "host helper: installed at sha-${PREVIOUS:0:12}, updating to sha-${TARGET:0:12}"
git -C "$ROOT" worktree add --detach -q "$SEED" "$PREVIOUS"
if [[ ! -f $SEED/scripts/mobius-rebuild-launcher.py ]]; then
  echo "host helper: sha-${PREVIOUS:0:12} predates the launcher; nothing to prove yet"
  exit 0
fi
seeded=$(revision_of "$SEED")
target_revision=$(revision_of "$ROOT")
printf 'SECRET_KEY=host-helper-regression-key-0123456789abcdef\nDOMAIN=localhost\n' >"$ENV_FILE"
chmod 0600 "$ENV_FILE"

echo "1. the previous release runs as an owner deploys it"
cd "$SEED"
MOBIUS_IMAGE="$IMAGE:sha-$PREVIOUS" docker compose --env-file "$ENV_FILE" \
  up -d --no-build --no-deps app
for _ in $(seq 1 60); do
  [[ $(docker inspect -f '{{.State.Health.Status}}' mobius 2>/dev/null) == healthy ]] && break
  sleep 5
done
[[ $(docker inspect -f '{{.State.Health.Status}}' mobius) == healthy ]] \
  || fail "the previous release did not become healthy"
# The worker's chat drain authenticates with the owner's service token.
docker exec mobius curl -fsS -o /dev/null -X POST -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"host-helper-owner-password"}' \
  http://127.0.0.1:8000/api/auth/setup || fail "owner setup failed"
for _ in $(seq 1 30); do
  docker exec mobius test -s /data/service-token.txt && break
  sleep 2
done
docker exec mobius test -s /data/service-token.txt || fail "the instance has no service token"

echo "2. the owner installs the helper once, from that checkout"
scripts/install-rebuild-helper.sh || fail "the installer failed"
[[ $(field "$STATUS" 'd.get("launcher_revision")') == 1 ]] \
  || fail "the installed helper is not the launcher"
[[ $(active_revision) == "$seeded" ]] || fail "the installed worker is not revision $seeded"

echo "3. the app requests the target release"
nonce=$(queue "$TARGET")
wait_status "$nonce" succeeded
replaced_with "$TARGET"
adoption=$(field "$STATUS" 'd.get("worker_adoption") or ""')
echo "   worker adoption: $adoption"
if (( target_revision > seeded )); then
  [[ $adoption == offered* ]] || fail "revision $target_revision was not offered"
fi

echo "4. the app requests the previous release again"
nonce=$(queue "$PREVIOUS")
wait_status "$nonce" succeeded
replaced_with "$PREVIOUS"
expected=$(( target_revision > seeded ? target_revision : seeded ))
# The worker reports before it exits; the launcher settles after it.
for _ in $(seq 1 60); do
  if ! systemctl is-active --quiet mobius-rebuild.service \
     && [[ $(active_revision) == "$expected" ]]; then break; fi
  sleep 2
done
[[ $(active_revision) == "$expected" ]] \
  || fail "worker revision $expected is not active (active: $(active_revision))"
[[ $(field "$STATUS" 'd.get("worker_revision")') == "$expected" ]] \
  || fail "revision $expected did not perform the replacement"
echo "host helper: worker revision $seeded -> $expected arrived with the image, replaced the container, and is active"
