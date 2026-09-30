"""POST /api/notifications/send: per-sender budgets and source-scoped tags."""

import pytest

from app import auth as auth_mod
from app import models
from app.routes.notifications import limiter as send_limiter


def _app(db, slug):
  app = models.App(
    slug=slug,
    source_dir=f"/tmp/mobius-tests/{slug}",
    name=slug, description="", jsx_source="export default function App(){}",
    compiled_path="/tmp/app.js",
  )
  db.add(app)
  db.commit()
  db.refresh(app)
  return app


def _app_auth(db, app):
  owner = db.query(models.Owner).one()
  token = auth_mod.create_app_token(
    app.id, owner.username, owner.token_epoch, app.token_nonce,
  )
  return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sent_pushes(db, auth, monkeypatch):
  owner = db.query(models.Owner).one()
  db.add(models.PushSubscription(
    id="sub-send", owner_id=owner.id, endpoint="https://push.example/send",
    p256dh="p256", auth="auth",
  ))
  db.commit()
  sent = []
  monkeypatch.setattr(
    "app.push.send_push", lambda _sub, payload: sent.append(payload) or True,
  )
  return sent


@pytest.fixture
def enforced_send_limit(monkeypatch):
  monkeypatch.setattr(send_limiter, "enabled", True)
  send_limiter.reset()
  yield
  send_limiter.reset()


def test_one_apps_burst_does_not_exhaust_other_senders_budget(
  client, auth, db, enforced_send_limit,
):
  """Every local sender shares one peer address; the budget must not."""
  busy = _app(db, "busy-chat-app")
  quiet = _app(db, "quiet-app")
  busy_auth = _app_auth(db, busy)

  statuses = [
    client.post(
      "/api/notifications/send", headers=busy_auth, json={"title": f"m{i}"},
    ).status_code
    for i in range(61)
  ]
  assert statuses[:60] == [200] * 60
  assert statuses[60] == 429

  assert client.post(
    "/api/notifications/send", headers=_app_auth(db, quiet),
    json={"title": "unaffected app"},
  ).status_code == 200
  assert client.post(
    "/api/notifications/send", headers=auth, json={"title": "unaffected owner"},
  ).status_code == 200


def test_app_tag_is_scoped_to_the_sending_app(client, db, sent_pushes):
  """An app cannot name another source's tag, even by spoofing its source."""
  app = _app(db, "tagging-app")
  response = client.post(
    "/api/notifications/send", headers=_app_auth(db, app),
    json={
      "title": "New message", "tag": "group:abc",
      "source_type": "system", "source_id": "999",
    },
  )
  assert response.status_code == 200, response.text
  assert sent_pushes[-1]["tag"] == f"app:{app.id}:group:abc"


def test_app_can_reuse_its_longest_target_intent_as_its_tag(
  client, db, sent_pushes,
):
  """The tag accepts exactly the shell's app-intent shape (1-128 chars)."""
  app = _app(db, "intent-tag-app")
  intent = "dm:" + "h" * 125
  response = client.post(
    "/api/notifications/send", headers=_app_auth(db, app),
    json={
      "title": "New message", "tag": intent,
      "target": f"/shell/?app={app.id}&intent={intent}",
    },
  )
  assert response.status_code == 200, response.text
  assert sent_pushes[-1]["tag"] == f"app:{app.id}:{intent}"


def test_untagged_push_carries_no_tag(client, auth, sent_pushes):
  assert client.post(
    "/api/notifications/send", headers=auth, json={"title": "Plain"},
  ).status_code == 200
  assert sent_pushes[-1]["tag"] is None


@pytest.mark.parametrize("tag", ["", "has space", "x" * 129, "a/b", "<tag>"])
def test_tag_outside_the_short_safe_charset_is_rejected(client, auth, tag):
  response = client.post(
    "/api/notifications/send", headers=auth, json={"title": "t", "tag": tag},
  )
  assert response.status_code == 422


@pytest.mark.parametrize("overrides", [
  {"title": "x" * 501},
  {"body": "x" * 8001},
  {"icon": "https://tracker.example/pixel.png"},
  {"icon": "//tracker.example/pixel.png"},
  # Browser URL parsing removes these controls, turning /<control>/ into //.
  {"icon": "/\n/tracker.example/pixel.png"},
  {"icon": "/\r/tracker.example/pixel.png"},
  {"icon": "/\t/tracker.example/pixel.png"},
  {"target": "/shell/?app=1&x=" + "x" * 2048},
  {"actions": [{"action": f"a{i}", "title": "Go"} for i in range(5)]},
  {"actions": [{"action": "open", "title": "x" * 201}]},
])
def test_app_notification_fields_are_bounded(client, auth, db, overrides):
  """An app token is untrusted code: it cannot store megabytes per notification
  in the owner's history or make the owner's device fetch an external icon."""
  headers = _app_auth(db, _app(db, "bounded-sender"))
  res = client.post(
    "/api/notifications/send", headers=headers,
    json={"title": "Ping", "body": "hello", **overrides},
  )
  assert res.status_code == 422, res.text
  assert client.get("/api/notifications", headers=auth).json() == []


def test_app_notification_accepts_same_origin_icon_and_readable_text(client, auth, db):
  headers = _app_auth(db, _app(db, "ordinary-sender"))
  res = client.post("/api/notifications/send", headers=headers, json={
    "title": "t" * 500, "body": "b" * 8000, "icon": "/icons/icon-192.png",
  })
  assert res.status_code == 200, res.text
  row = client.get("/api/notifications", headers=auth).json()[0]
  assert row["icon"] == "/icons/icon-192.png"
