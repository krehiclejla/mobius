"""Core delegation admission without an installed owner app."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import models
from app.delegations import delegation_execution_token, policy_for_chat
from test_app_fixtures import create_local_app
from tests.goal_fixtures import goal_run as make_goal_run


def _parent_with_run(client, owner_token, db):
  auth = {"Authorization": f"Bearer {owner_token}"}
  response = client.post("/api/chats", json={"title": "Parent"}, headers=auth)
  assert response.status_code == 200, response.text
  chat_id = response.json()["id"]
  db.add(make_goal_run(db, id="core-parent-run", root_run_id="core-parent-root",
                       chat_id=chat_id, status="running", provider="codex"))
  db.commit()
  return chat_id


def _submit(client, auth, parent, *, name="review", app_id=None):
  return client.post("/api/delegations", json={
    "app_id": app_id, "parent_chat_id": parent, "task_key": name,
    "prompt": "Review one bounded task.", "provider": "codex", "scope": "read",
  }, headers=auth)


def test_core_submit_attaches_without_app_and_nested_child_inherits_none(
  client, owner_token, db, monkeypatch,
):
  auth = {"Authorization": f"Bearer {owner_token}"}
  parent = _parent_with_run(client, owner_token, db)
  async def fake_start(**_kwargs):
    return True
  monkeypatch.setattr("app.routes.delegations.start_programmatic_chat_turn", fake_start)

  owner_capabilities = client.get("/api/delegations/capabilities", headers=auth)
  assert owner_capabilities.status_code == 200, owner_capabilities.text
  assert owner_capabilities.json()["app_id"] is None

  first = _submit(client, auth, parent)
  assert first.status_code == 201, first.text
  assert first.json()["app_id"] is None
  second = _submit(client, auth, parent)
  assert second.status_code == 201, second.text
  assert second.json()["attached"] is True
  assert second.json()["id"] == first.json()["id"]

  child = first.json()["child_chat_id"]
  db.add(make_goal_run(db, id="core-child-run", root_run_id="core-child-run",
                       chat_id=child, status="running", provider="codex"))
  db.commit()
  policy = policy_for_chat(db, child)
  assert policy is not None and policy.app_id is None
  child_auth = {"Authorization": f"Bearer {delegation_execution_token(db, policy)}"}
  capabilities = client.get("/api/delegations/capabilities", headers=child_auth)
  assert capabilities.status_code == 200, capabilities.text
  nested = _submit(client, child_auth, child, name="nested")
  assert nested.status_code == 201, nested.text
  assert nested.json()["app_id"] is None
  assert db.query(models.Delegation).count() == 2
  denied = client.post("/api/delegations", json={
    "parent_chat_id": child, "task_key": "write", "prompt": "Write.",
    "provider": "codex", "scope": "write",
  }, headers=child_auth)
  assert denied.status_code == 403


def test_app_owned_nested_submission_inherits_app_but_core_remains_core(
  client, owner_token, db, monkeypatch,
):
  auth = {"Authorization": f"Bearer {owner_token}"}
  app_id = create_local_app(client, auth, name="Owner app")["id"]
  parent = _parent_with_run(client, owner_token, db)
  async def fake_start(**_kwargs):
    return True
  monkeypatch.setattr("app.routes.delegations.start_programmatic_chat_turn", fake_start)
  owned = _submit(client, auth, parent, app_id=app_id)
  assert owned.status_code == 201, owned.text
  core = _submit(client, auth, parent, name="core")
  assert core.status_code == 201, core.text
  assert core.json()["app_id"] is None
  child = owned.json()["child_chat_id"]
  db.add(make_goal_run(db, id="app-child-run", root_run_id="app-child-run",
                       chat_id=child, status="running", provider="codex"))
  db.commit()
  policy = policy_for_chat(db, child)
  child_auth = {"Authorization": f"Bearer {delegation_execution_token(db, policy)}"}
  nested = _submit(client, child_auth, child, name="owned-nested")
  assert nested.status_code == 201, nested.text
  assert nested.json()["app_id"] == app_id
  wrong = _submit(client, child_auth, child, name="wrong", app_id=app_id + 999)
  assert wrong.status_code == 403


def test_core_tool_reattaches_preupgrade_app_owned_task_without_reassigning_it(
  client, owner_token, db, monkeypatch,
):
  from app.timeutil import now_naive_utc
  auth = {"Authorization": f"Bearer {owner_token}"}
  app_id = create_local_app(client, auth, name="Subagents")["id"]
  parent = _parent_with_run(client, owner_token, db)
  async def fake_start(**_kwargs):
    return True
  monkeypatch.setattr("app.routes.delegations.start_programmatic_chat_turn", fake_start)
  original = _submit(client, auth, parent, app_id=app_id)
  assert original.status_code == 201, original.text
  attached = _submit(client, auth, parent)
  assert attached.status_code == 201, attached.text
  assert attached.json()["attached"] is True
  assert attached.json()["id"] == original.json()["id"]
  assert attached.json()["app_id"] == app_id
  assert db.query(models.Delegation).count() == 1
  # Null ownership is not a way to bypass an old task's deleted app owner.
  db.get(models.App, app_id).deleted_at = now_naive_utc()
  db.commit()
  refused = _submit(client, auth, parent)
  assert refused.status_code == 404
  assert db.query(models.Delegation).count() == 1


def test_cancelled_core_parent_cannot_create_more_nested_work(client, owner_token, db, monkeypatch):
  from app.delegations import mark_cancelled
  auth = {"Authorization": f"Bearer {owner_token}"}
  parent = _parent_with_run(client, owner_token, db)
  async def fake_start(**_kwargs):
    return True
  monkeypatch.setattr("app.routes.delegations.start_programmatic_chat_turn", fake_start)
  first = _submit(client, auth, parent)
  assert first.status_code == 201, first.text
  child = first.json()["child_chat_id"]
  db.add(make_goal_run(db, id="core-child-run", root_run_id="core-child-run",
                       chat_id=child, status="running", provider="codex"))
  db.commit()
  token = delegation_execution_token(db, policy_for_chat(db, child))
  row = db.get(models.Delegation, first.json()["id"])
  mark_cancelled(db, row)
  response = _submit(client, {"Authorization": f"Bearer {token}"}, child, name="too-late")
  assert response.status_code == 403
  assert db.query(models.Delegation).count() == 1


def test_only_live_installed_app_preferences_overlay_core_defaults(client, owner_token, db):
  import json
  from pathlib import Path
  from app.config import get_settings
  from app.timeutil import now_naive_utc
  auth = {"Authorization": f"Bearer {owner_token}"}
  app_id = create_local_app(client, auth, name="Subagents")["id"]
  row = db.get(models.App, app_id)
  row.slug = "subagents"
  db.commit()
  root = Path(get_settings().data_dir) / "apps" / str(app_id)
  root.mkdir(parents=True, exist_ok=True)
  preferences = {"providers": {"codex": {"enabled": False, "default_model": "chosen"}}}
  (root / "config.json").write_text(json.dumps(preferences))
  before = client.get("/api/delegations/capabilities", headers=auth)
  assert before.status_code == 200, before.text
  assert before.json()["config"] == preferences
  row.deleted_at = now_naive_utc()
  db.commit()
  after = client.get("/api/delegations/capabilities", headers=auth)
  assert after.status_code == 200, after.text
  assert after.json()["config"] == {} and after.json()["app_id"] is None
  assert (root / "config.json").read_text() == json.dumps(preferences)


@pytest.mark.parametrize("gate", ["app", "parent"])
@pytest.mark.parametrize("advance_root", [False, True])
def test_legacy_owner_attachment_pins_root_while_waiting_for_admission(
  client, owner_token, db, monkeypatch, gate, advance_root,
):
  from app.deps import Principal
  from app.routes.delegations import DelegationSubmit, submit_or_attach

  auth = {"Authorization": f"Bearer {owner_token}"}
  app_id = create_local_app(client, auth, name="Legacy owner")["id"]
  parent = _parent_with_run(client, owner_token, db)
  starts = []
  async def fake_start(**kwargs):
    starts.append(kwargs)
    return True
  monkeypatch.setattr(
    "app.routes.delegations.start_programmatic_chat_turn", fake_start,
  )
  original = _submit(client, auth, parent, app_id=app_id)
  assert original.status_code == 201, original.text
  starts.clear()

  from app import chat_queue
  original_lock = chat_queue.get_transition_lock

  async def race():
    lock = asyncio.Lock()
    waiting = asyncio.Event()
    blocked_key = f"app-lifecycle:{app_id}" if gate == "app" else parent

    @asynccontextmanager
    async def transition_lock(key):
      if key == blocked_key:
        waiting.set()
        async with lock:
          yield
      else:
        yield

    monkeypatch.setattr(
      "app.chat_queue.get_transition_lock", transition_lock,
    )
    body = DelegationSubmit(
      parent_chat_id=parent, task_key="review",
      prompt="Review one bounded task.", provider="codex", scope="read",
    )
    async with lock:
      task = asyncio.create_task(submit_or_attach(
        body,
        principal=Principal(owner=db.query(models.Owner).first(), app_id=None),
        db=db,
      ))
      await asyncio.wait_for(waiting.wait(), timeout=5)
      assert not task.done()
      if advance_root:
        with Session(db.get_bind()) as writer:
          writer.get(models.ChatRun, "core-parent-run").status = "completed"
          writer.add(make_goal_run(
            writer, id="next-parent-run", root_run_id="next-parent-root",
            chat_id=parent, status="running", provider="codex",
          ))
          writer.commit()
    if advance_root:
      with pytest.raises(HTTPException) as rejected:
        await task
      assert rejected.value.status_code == 409
      assert "parent chat run changed" in rejected.value.detail
      assert starts == []
    else:
      attached = await task
      assert attached["attached"] is True
      assert attached["id"] == original.json()["id"]
      assert attached["app_id"] == app_id

  asyncio.run(race())
  monkeypatch.setattr(chat_queue, "get_transition_lock", original_lock)
  assert db.query(models.Delegation).count() == 1
  if advance_root:
    # A fresh request belongs to the new root, not the old app owner.
    fresh = _submit(client, auth, parent)
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["app_id"] is None
    assert fresh.json()["attached"] is False
