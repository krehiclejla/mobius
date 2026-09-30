#!/usr/bin/env python3
"""Frozen root launcher for the self-hosted Möbius replacement worker.

Installed once as ``/usr/local/libexec/mobius-rebuild-host`` and never changed
by updates. It holds no replacement logic. It runs a copy of
``scripts/mobius-rebuild-host.py`` recorded in ``workers.json``:

- ``active``: the proven worker. It always runs ``reconcile`` and runs a
  replacement when there is no candidate.
- ``candidate``: a newer worker the active one took from a verified official
  image after a successful replacement. The launcher removes it from the
  record before trying it on the next replacement, so a trial the host
  interrupts is never repeated. It becomes active only when that replacement
  succeeds; the worker's revision high-water mark keeps it from ever being
  offered again otherwise.

Worker changes therefore ship in releases and activate without a host command,
and a faulty one costs one attempt. Only a change to this file needs a
reinstall (``deployment/self-hosted-helper.required``).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

LAUNCHER_REVISION = 1
STATE_DIR = Path("/var/lib/mobius-rebuild")
WORKERS = STATE_DIR / "workers"
INDEX = STATE_DIR / "workers.json"
STATUS = STATE_DIR / "status.json"
LOCK = STATE_DIR / "replace.lock"
PYTHON = "/usr/bin/python3"
ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "MOBIUS_REBUILD_LAUNCHER": str(LAUNCHER_REVISION),
}


def _root_private(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    kind = 0o040000 if directory else 0o100000
    return (
        info.st_mode & 0o170000 == kind and info.st_uid == 0
        and info.st_mode & 0o077 == 0
    )


def _worker(entry) -> dict | None:
    """One recorded worker, only if its file is private and unchanged."""
    try:
        path = WORKERS / str(entry["file"])
        digest = str(entry["sha256"])
    except (KeyError, TypeError):
        return None
    if path.parent != WORKERS or not _root_private(path, directory=False):
        return None
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return None
    except OSError:
        return None
    return {**entry, "path": path}


def load_index() -> dict | None:
    if not (_root_private(WORKERS, directory=True)
            and _root_private(INDEX, directory=False)):
        return None
    try:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(index, dict) or index.get("version") != 1:
        return None
    active = _worker(index.get("active"))
    if active is None:
        return None
    candidate = _worker(index["candidate"]) if index.get("candidate") else None
    return {**index, "active": active, "candidate": candidate}


def _record(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "path"}


def _update(change) -> None:
    """Apply ``change`` to the stored record under the replacement lock."""
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stored = json.loads(INDEX.read_text(encoding="utf-8"))
        change(stored)
        fd, name = tempfile.mkstemp(dir=STATE_DIR, prefix=".workers.json.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(stored, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, 0o600)
            os.replace(name, INDEX)
            directory = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)


def _status() -> bytes:
    try:
        return STATUS.read_bytes()
    except OSError:
        return b""


def execute(worker: dict, command: str) -> int:
    return subprocess.run(
        [PYTHON, "-I", "-S", str(worker["path"]), command],
        env=ENV, cwd="/", check=False,
    ).returncode


def try_candidate(candidate: dict, replaced: str) -> int:
    """Run one replacement with the candidate, taken out of the record first."""
    digest = candidate["sha256"]
    taken = []


    def take(stored):
        if (stored.get("candidate") or {}).get("sha256") == digest:
            stored["candidate"] = None
            taken.append(True)

    _update(take)
    if not taken:
        return 1  # the record changed first; the next run reads it afresh
    before = _status()
    result = execute(candidate, "run")
    after = _status()
    try:
        state = json.loads(after).get("state") if after != before else None
    except ValueError:
        state = None

    def settle(stored):
        if (stored.get("active") or {}).get("sha256") != replaced:
            return  # a newer worker was installed while it ran
        if after == before and result == 0:
            # Nothing was queued: the candidate has not been tried, and it
            # goes back unless a newer worker was offered meanwhile.
            if stored.get("high_water") == candidate["revision"]:
                stored["candidate"] = _record(candidate)
        elif state in {"succeeded", "no_change"}:
            stored["active"] = _record(candidate)

    _update(settle)
    return result


def main(argv: list[str]) -> int:
    os.umask(0o077)
    if (os.geteuid() != 0 or len(argv) != 2
            or argv[1] not in {"run", "reconcile"}):
        print("invalid invocation", file=sys.stderr)
        return 2
    index = load_index()
    if index is None:
        print("no verified replacement worker is installed; rerun "
              "scripts/install-rebuild-helper.sh", file=sys.stderr)
        return 1
    if argv[1] == "run" and index["candidate"]:
        return try_candidate(index["candidate"], index["active"]["sha256"])
    return execute(index["active"], argv[1])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
