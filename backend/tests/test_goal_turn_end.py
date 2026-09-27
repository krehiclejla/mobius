"""An ended Goal turn needs no handoff: nothing checks it, nothing continues it.

Replaces the retired owner-handoff settlement (``goal_terminal_handoff`` and
the "Goal needs reconciliation" card). A Goal moves only through what already
wakes a chat; an idle unfinished Goal is simply the owner's turn.
"""

from tests.goal_fixtures import goal_run as make_goal_run

import pytest

from app import models
from app.chat_writer import PromotePending, get_writer


UNFINISHED = {"version": 1, "tasks": [{
  "id": "finish", "title": "Finish", "status": "running",
  "depends_on": [],
}]}
SETTLED = {"version": 1, "tasks": [{
  "id": "finish", "title": "Finish", "status": "completed",
  "depends_on": [],
}]}


def _add_goal_run(db, chat, run_id="goal-run", *, goal_id="goal-run", plan=UNFINISHED):
  if not chat.messages:
    chat.messages = [{
      "role": "user", "content": "Finish the work", "cid": "owner-request",
      "ts": 1,
    }]
  db.add(make_goal_run(db,
    id=run_id,
    root_run_id=run_id,
    chat_id=chat.id,
    status="running",
    provider="codex",
    goal_objective="Finish the work",
    goal_id=goal_id,
    goal_plan_json=plan,
    goal_plan_revision=1,
  ))
  db.commit()


def _question_blocks(chat):
  return [
    block
    for message in chat.messages or []
    for block in message.get("blocks") or []
    if block.get("type") == "question"
  ]


@pytest.mark.asyncio
@pytest.mark.parametrize("closing_text", [
  "The Goal is complete.",
  "I'll wait for you to say how you want to split the work.",
  "The reviewed change is ready; Send PR in Contribute is the next step.",
])
async def test_a_goal_turn_ending_in_plain_text_posts_no_card_and_starts_nothing(
  db, chat, monkeypatch, closing_text,
):
  """The real endings that used to raise "Goal needs reconciliation"."""
  from app import chat as chat_mod, chat_queue
  from app.broadcast import create_broadcast, remove_broadcast
  from app.chat_event_sink import ChatEventSink

  _add_goal_run(db, chat)
  scheduled = []
  monkeypatch.setattr(
    chat_mod, "_schedule_continuation", lambda **kwargs: scheduled.append(kwargs),
  )
  monkeypatch.setattr(chat_mod, "_publish_chat_run_finished", lambda *_: None)
  broadcast = create_broadcast(chat.id)
  sink = ChatEventSink(broadcast, chat.id, run_token="goal-run")
  sink.publish({"type": "text", "content": closing_text})
  try:
    disposition = await chat_mod._complete_turn(
      bc=broadcast, sink=sink, db=db, chat_id=chat.id, run_gen=None,
      provider_id="codex", cost_usd=0, close_browser=False,
    )
  finally:
    remove_broadcast(chat.id)

  assert disposition is chat_queue.TerminalDisposition.EMPTY_TERMINAL_CLEARED
  assert scheduled == []
  db.expire_all()
  saved = db.get(models.Chat, chat.id)
  assert saved.pending_question_id is None
  assert saved.pending_messages in (None, [])
  assert _question_blocks(saved) == []
  assert db.get(models.ChatGoal, "goal-run").status == "open"


def test_terminal_promotion_never_manufactures_a_goal_successor(db, chat):
  _add_goal_run(db, chat)

  result = get_writer().submit(PromotePending(
    chat_id=chat.id, run_token="successor", ending_status="completed",
  )).result(timeout=5)

  assert result["promoted"] is None
  db.expire_all()
  assert db.get(models.Chat, chat.id).pending_messages in (None, [])
  assert db.get(models.ChatRun, "successor") is None


@pytest.mark.asyncio
async def test_wait_delivery_starts_only_its_exact_goal_run(db, chat, monkeypatch):
  from app import chat as chat_mod, chat_waits

  _add_goal_run(db, chat)
  wait = chat_waits.declare_wait(
    db, chat_id=chat.id, created_by_run_id="goal-run",
    description="External gate finished", kind="timer", delay_secs=60,
  )
  wait.status = "met"
  db.get(models.ChatRun, "goal-run").status = "completed"
  db.commit()
  scheduled = []
  monkeypatch.setattr(chat_mod, "_schedule_continuation", lambda **kw: scheduled.append(kw))

  assert await chat_waits._deliver_resume(wait.id) is True
  assert await chat_waits._deliver_resume(wait.id) is False

  db.expire_all()
  assert len(scheduled) == 1
  successor = db.get(models.ChatRun, f"wait-resume-{wait.id}")
  assert successor.goal_id == "goal-run"
  assert db.get(models.ChatWait, wait.id).resume_delivered_at is not None


def _complete(db, result="Verified: checks green"):
  from app.goals import update_goal_record

  goal = db.get(models.ChatGoal, "goal-run")
  return update_goal_record(
    db, db.get(models.ChatRun, "goal-run"), goal, goal.revision, result=result,
  )


@pytest.mark.parametrize("outcome", ["met", "expired", "failed"])
def test_verified_completion_takes_delivery_of_its_fired_wait(db, chat, outcome):
  """The running turn saw the outcome its Wait was watching for.

  The Wait's resume could only be delivered after this turn; the verified
  completion is that delivery, so no resume later wakes the finished Goal.
  """
  from app import chat_waits

  _add_goal_run(db, chat, plan=SETTLED)
  wait = chat_waits.declare_wait(
    db, chat_id=chat.id, created_by_run_id="goal-run",
    description="Upstream checks finishing", kind="timer", delay_secs=60,
  )
  wait.status = outcome
  db.commit()

  assert _complete(db)["status"] == "completed"
  db.expire_all()
  assert db.get(models.ChatWait, wait.id).resume_delivered_at is not None


def test_an_armed_wait_does_not_block_completion_and_keeps_watching(db, chat):
  """Done is one action; a Wait the agent armed still does what it was set to."""
  from app import chat_waits

  _add_goal_run(db, chat, plan=SETTLED)
  armed = chat_waits.declare_wait(
    db, chat_id=chat.id, created_by_run_id="goal-run",
    description="Release gate", kind="timer", delay_secs=60,
  )
  db.commit()

  assert _complete(db)["status"] == "completed"
  db.expire_all()
  assert db.get(models.ChatWait, armed.id).status == "armed"


def test_an_open_owner_card_does_not_block_completion(db, chat):
  _add_goal_run(db, chat, plan=SETTLED)
  chat.messages = [*chat.messages, {
    "role": "assistant", "id": "goal-run:assistant:1", "ts": 2, "content": "",
    "blocks": [{
      "type": "question", "question_id": "merge-approval",
      "response_mode": "continuation",
      "questions": [{"id": "q", "question": "Merge it?", "options": []}],
    }],
  }]
  chat.pending_question_id = "merge-approval"
  db.commit()

  assert _complete(db)["status"] == "completed"
  db.expire_all()
  assert db.get(models.Chat, chat.id).pending_question_id == "merge-approval"


def test_unfinished_tasks_still_block_completion(db, chat):
  from app.goal_plans import GoalPlanError

  _add_goal_run(db, chat, plan=UNFINISHED)

  with pytest.raises(GoalPlanError, match="unfinished tasks"):
    _complete(db)


def test_completion_withdraws_the_queued_resume_of_its_fired_wait(db, chat):
  """The Wait fired mid-turn and queued its resume; the same turn then verified
  the outcome and completed. The queued resume is now stale and must not start
  a turn for the finished Goal, while other queued messages stay."""
  import asyncio

  from app import chat_waits
  from app.goals import settle_after_goal_completion

  _add_goal_run(db, chat, plan=SETTLED)
  wait = chat_waits.declare_wait(
    db, chat_id=chat.id, created_by_run_id="goal-run",
    description="Checks finishing", kind="timer", delay_secs=60,
  )
  wait.status = "met"
  chat.pending_messages = [{
    "role": "user", "content": "A wait you declared has completed.",
    "ts": 1, "cid": f"wait-result-{wait.id}", "hidden": True,
    "kind": "wait_result", "source_work_id": "goal-run",
  }, {"role": "user", "content": "Owner follow-up", "ts": 2, "cid": "owner-1"}]
  db.commit()

  assert _complete(db)["status"] == "completed"
  asyncio.run(settle_after_goal_completion(chat.id))

  db.expire_all()
  assert db.get(models.ChatWait, wait.id).resume_delivered_at is not None
  assert [m["cid"] for m in db.get(models.Chat, chat.id).pending_messages] == [
    "owner-1",
  ]


def test_a_planless_goal_cannot_complete_while_its_helper_works(db, chat, monkeypatch):
  """Done waits for running helpers whether or not the Goal has a plan."""
  from app import goal_plans
  from app.goal_plans import GoalPlanError

  _add_goal_run(db, chat, plan=None)
  helper = {"id": "d1", "task_key": "review", "status": "running", "children": []}
  monkeypatch.setattr(goal_plans, "_delegation_tree", lambda *_: [helper])

  with pytest.raises(GoalPlanError, match="active delegations"):
    _complete(db)
  assert db.get(models.ChatGoal, "goal-run").status == "open"

  helper["status"] = "completed"
  assert _complete(db)["status"] == "completed"


def _legacy_goal_handoff(cid="goal-handoff-old-run"):
  """The hidden control the pre-2026-09-27 writer queued to keep a Goal going."""
  return {
    "role": "user", "content": "Continue the unfinished Goal from its saved plan.",
    "kind": "continuation", "continuation_reason": "goal_handoff",
    "goal_id": "goal-run", "hidden": True, "cid": cid, "ts": 5,
  }


def test_a_lone_legacy_goal_handoff_is_retired_unrun(db, chat):
  _add_goal_run(db, chat)
  chat.pending_messages = [_legacy_goal_handoff()]
  db.commit()

  result = get_writer().submit(PromotePending(
    chat_id=chat.id, run_token="successor",
  )).result(timeout=5)

  assert result["promoted"] is None
  db.expire_all()
  saved = db.get(models.Chat, chat.id)
  assert saved.pending_messages == []
  assert not any(
    "Continue the unfinished Goal" in str(m.get("content")) for m in saved.messages
  )
  assert db.get(models.ChatRun, "successor") is None


def test_a_legacy_goal_handoff_behind_owner_input_never_runs(db, chat):
  _add_goal_run(db, chat)
  chat.pending_messages = [
    {"role": "user", "content": "Owner follow-up", "cid": "owner-1", "ts": 4},
    _legacy_goal_handoff(),
  ]
  db.commit()

  result = get_writer().submit(PromotePending(
    chat_id=chat.id, run_token="successor",
  )).result(timeout=5)

  assert result["promoted"]["content"] == "Owner follow-up"
  db.expire_all()
  saved = db.get(models.Chat, chat.id)
  assert saved.pending_messages == []
  assert not any(
    "Continue the unfinished Goal" in str(m.get("content")) for m in saved.messages
  )


def test_stop_retires_a_legacy_goal_handoff_without_resending_it(db, chat):
  from app.chat_writer import ClearPending

  wait_result = {
    "role": "user", "content": "A wait you declared has completed.",
    "kind": "wait_result", "hidden": True, "cid": "wait-result-1", "ts": 6,
  }
  chat.pending_messages = [
    {"role": "user", "content": "Owner text", "cid": "owner-1", "ts": 4},
    _legacy_goal_handoff(),
    wait_result,
  ]
  db.commit()

  cleared = get_writer().submit(ClearPending(
    chat_id=chat.id, run_token="",
  )).result(timeout=5)

  assert cleared == {"cleared": 2, "cleared_cids": ["owner-1"]}
  db.expire_all()
  assert db.get(models.Chat, chat.id).pending_messages == [wait_result]
