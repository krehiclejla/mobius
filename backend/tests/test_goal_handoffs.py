"""Native handoffs belong to one exact Goal, even when its chat is reused."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app import models
from app.chat_handoffs import goal_handoffs
from app.goal_plans import _goal_handoff
from tests.goal_fixtures import goal_run


def _run(db, chat, run_id, goal_id):
  row = goal_run(db, id=run_id, chat_id=chat.id, status="completed",
    provider="codex", goal_id=goal_id, goal_objective="Finish it")
  db.add(row)
  db.commit()
  return row, db.get(models.ChatGoal, goal_id)


def test_armed_and_owed_waits_belong_to_the_exact_goal(db, chat):
  first, goal_a = _run(db, chat, "first-attempt", "goal-a")
  db.add(models.ChatWait(id="armed-a", chat_id=chat.id,
    created_by_run_id=first.id, goal_id=goal_a.id, root_run_id=first.id,
    kind="timer", status="armed", description="Wait A",
    deadline_at=datetime.now(UTC), next_check_at=datetime.now(UTC)))
  db.add(models.ChatWait(id="owed-a", chat_id=chat.id,
    created_by_run_id=first.id, goal_id=goal_a.id, root_run_id=first.id,
    kind="timer", status="met", description="Result A",
    deadline_at=datetime.now(UTC), next_check_at=datetime.now(UTC)))
  second, goal_b = _run(db, chat, "second-attempt", "goal-b")

  owned = goal_handoffs(db, goal_a)
  assert len(owned.waits) == 2 and owned.prevents_settlement
  assert {wait.wait_id for wait in owned.waits} == {"armed-a", "owed-a"}
  assert {wait.kind for wait in owned.waits} == {"timer"}
  assert any(wait.delivery_pending for wait in owned.waits)
  assert goal_handoffs(db, goal_b).waits == ()
  assert not goal_handoffs(db, goal_b).prevents_settlement
  assert _goal_handoff(db, second, goal_b) == {"kind": "none", "reason": None}


def test_saved_card_is_only_the_current_goal_handoff(db, chat):
  first, goal_a = _run(db, chat, "card-first", "card-goal-a")
  second, goal_b = _run(db, chat, "card-second", "card-goal-b")
  chat.pending_question_id = "pending-choice"
  db.commit()

  assert not goal_handoffs(db, goal_a).owner_input
  assert goal_handoffs(db, goal_b).card.question_id == "pending-choice"
  assert goal_handoffs(db, goal_b).card.kind == "saved"
  assert _goal_handoff(db, second, goal_b) == {
    "kind": "owner_input", "reason": "saved_card",
  }


def test_legacy_helper_exact_goal_root_prevents_settlement(db, chat, monkeypatch):
  from app import delegations
  _run(db, chat, "helper-attempt", "helper-goal")
  goal = db.get(models.ChatGoal, "helper-goal")
  rows = [
    (SimpleNamespace(id="own-helper", goal_id=None, parent_root_run_id=goal.id), "running"),
    (SimpleNamespace(id="foreign-helper", goal_id=None, parent_root_run_id="other-goal"), "running"),
  ]
  monkeypatch.setattr(delegations, "_self_resuming_helper_rows", lambda *_: rows)

  native = goal_handoffs(db, goal)
  assert native.helper_count == 1 and native.prevents_settlement
  assert native.helpers[0].delegation_id == "own-helper"
  assert _goal_handoff(db, db.get(models.ChatRun, "helper-attempt"), goal) == {
    "kind": "automatic", "reason": None,
  }


def test_continuation_park_keeps_its_run_identity(db, chat, monkeypatch):
  from app import chat as chat_module
  run, goal = _run(db, chat, "parked-attempt", "parked-goal")
  run.status = "parked"
  run.park_reason = "memory"
  db.commit()
  monkeypatch.setattr(chat_module, "continuation_handoff_for_chat",
    lambda *_: {"kind": "automatic", "reason": "memory"})

  native = goal_handoffs(db, goal)
  assert native.park.run_id == run.id
  assert native.park.kind == "automatic" and native.prevents_settlement
  assert _goal_handoff(db, run, goal) == {"kind": "automatic", "reason": "memory"}


def test_owner_hold_presentation_does_not_become_an_automatic_handoff(db, chat):
  run, goal = _run(db, chat, "held-attempt", "held-goal")
  goal.status = "stopped"
  goal.hold_json = {
    "cause": "stop", "actor": "owner", "source_id": "owner-stop",
    "run_id": run.id, "actor_id": None, "at": datetime.now(UTC).isoformat(),
  }
  db.commit()
  assert _goal_handoff(db, run, goal) == {"kind": "owner_hold", "reason": "owner"}


def test_claim_dependency_is_exact_goal_handoff_and_blocks_settlement(db, chat, monkeypatch):
  from app import goal_dependencies
  first, goal_a = _run(db, chat, "dependency-first", "dependency-a")
  second, goal_b = _run(db, chat, "dependency-second", "dependency-b")
  def projection(_db, goal, *, include_foreign_context):
    assert include_foreign_context is True
    return [{"id": "follow-a", "target_kind": "claim_acquisition", "condition": "Host repair",
      "source_chat_id": "source-chat", "source_chat_title": "Watch platform update",
      "automatic": True, "delivery_pending": False, "resume_blocker": None, "outcome": None}] if goal.id == goal_a.id else []
  monkeypatch.setattr(goal_dependencies, "goal_dependency_handoffs", projection)

  native = goal_handoffs(db, goal_a)
  assert native.prevents_settlement and native.dependencies[0].id == "follow-a"
  assert not goal_handoffs(db, goal_b).prevents_settlement
  assert _goal_handoff(db, first, goal_a) == {"kind": "automatic", "reason": "dependency"}
  assert _goal_handoff(db, second, goal_b) == {"kind": "none", "reason": None}


def test_lost_claim_source_is_recovery_not_healthy_handoff(db, chat, monkeypatch):
  from app import goal_dependencies
  run, goal = _run(db, chat, "dependency-lost", "dependency-lost-goal")
  def projection(_db, _goal, *, include_foreign_context):
    assert include_foreign_context is True
    return [{
      "id": "lost", "target_kind": "claim_acquisition", "condition": "Host repair",
      "source_chat_id": None, "source_chat_title": None, "automatic": False,
      "delivery_pending": False, "resume_blocker": "source_missing", "outcome": None,
    }]
  monkeypatch.setattr(goal_dependencies, "goal_dependency_handoffs", projection)
  assert not goal_handoffs(db, goal).prevents_settlement
  assert _goal_handoff(db, run, goal) == {"kind": "recovery", "reason": "dependency_source"}


def test_owed_claim_result_remains_a_handoff_until_receipt(db, chat, monkeypatch):
  from app import goal_dependencies
  run, goal = _run(db, chat, "dependency-owed", "dependency-owed-goal")
  row = {"id": "owed", "target_kind": "claim_acquisition", "condition": "Host repair",
    "source_chat_id": "source", "source_chat_title": "Watch platform update",
    "automatic": True, "delivery_pending": True, "resume_blocker": None,
    "outcome": {"kind": "completed"}}
  def projection(_db, _goal, *, include_foreign_context):
    assert include_foreign_context is True
    return [row]
  monkeypatch.setattr(goal_dependencies, "goal_dependency_handoffs", projection)
  assert goal_handoffs(db, goal).prevents_settlement
  assert _goal_handoff(db, run, goal) == {"kind": "automatic", "reason": "dependency"}
  row["delivery_pending"] = False
  assert not goal_handoffs(db, goal).prevents_settlement
