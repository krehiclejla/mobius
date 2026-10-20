"""A foreign plain decision owns the handoff without a duplicate card/claim."""
import asyncio

import pytest

from app import chat as chat_mod, models, questions
from app.chat_writer import (create_chat, AnswerQuestion, FinishRun, QuestionCommit,
  StartTurn, AdmitProviderExecution, AcknowledgeProviderSuccess, get_writer)
from app.decision_cards import (saved_decision, stage_wait_for_decision,
  stage_delete_decisions)
from app import goal_dependencies as deps
from app.chat_handoffs import goal_handoffs
from app.timeutil import now_naive_utc


def _setup(db, chat, *, family=None, quiet=False):
  source = create_chat(id="source", title="Repair decision", messages=[])
  db.add(source)
  db.add(models.ChatGoal(id="dependent-goal", chat_id=chat.id, objective="Finish update"))
  db.add(models.ChatRun(id="dependent-run", root_run_id="dependent-run", chat_id=chat.id,
    goal_id="dependent-goal", status="completed", provider="claude"))
  db.add(models.ChatGoal(id="source-goal", chat_id=source.id, objective="Choose repair"))
  db.commit()
  get_writer().submit(StartTurn(chat_id=source.id, run_token="source-run",
    user_msg={"role": "user", "content": "Choose repair", "ts": 1})).result(5)
  db.expire_all()
  db.get(models.ChatRun, "source-run").goal_id = "source-goal"
  db.commit()
  card = {"type": "question", "question_id": "choice", "response_mode": "continuation",
    "questions": [{"id": "q", "question": "Which release?", "options": [
      {"id": "later", "label": "Not now", **({"on_answer": "close"} if quiet else {})},
      {"id": "yes", "label": "Build and test"}]}]}
  if family == "approval":
    card["action_key"] = "update:repair"
  elif family == "restart":
    card["platform_action"] = {"kind": "restart"}
  elif family == "sealed":
    card["secure_input"] = {"request_id": "secret", "value": "never-copy"}
  get_writer().submit(QuestionCommit(chat_id=source.id, run_token="source-run",
    snapshot={"id": "source-run", "role": "assistant", "blocks": [card], "ts": 2})).result(5)
  db.expire_all()
  return source, card


def _follow(db, chat, qid="choice"):
  row = stage_wait_for_decision(db, chat_id=chat.id, run_id="dependent-run",
    source_chat_id="source", question_id=qid)
  db.commit()
  return row


def _answer(*, quiet=False):
  get_writer().submit(AnswerQuestion(chat_id="source", question_id="choice",
    answers={"q": "Not now"}, selected_options={"q": ["later"]},
    close_without_reply=quiet)).result(5)


@pytest.mark.parametrize("incident", ["13:33", "16:48"])
def test_plain_foreign_question_is_one_handoff_and_one_exact_goal_continuation(db, chat, monkeypatch, incident):
  source, card = _setup(db, chat)
  row = _follow(db, chat)
  assert _follow(db, chat).id == row.id
  handoff = goal_handoffs(db, db.get(models.ChatGoal, "dependent-goal"))
  assert handoff.prevents_settlement and not handoff.owner_input
  from app.chat_writer import PromotePending
  from app import transcript_rows
  response = get_writer().submit(PromotePending(chat_id=chat.id, run_token="must-not-settle",
    ending_run_token="dependent-run", ending_status="completed")).result(5)
  assert response["promoted"] is None
  assert db.get(models.ChatRun, "must-not-settle") is None
  assert not any("goal_settlement_unfinished" in str(message) for message in transcript_rows.history(chat))
  assert db.query(models.AgentWorkClaim).count() == 0
  assert len([c for c in (questions.saved_question(source, "choice"),) if c]) == 1
  _answer()
  db.expire_all()
  assert row.outcome_json["kind"] == "answered"
  assert row.outcome_json["answers"] == {"q": "Not now"}
  assert row.outcome_json["selected_options"] == {"q": ["later"]}
  db.add(models.ChatGoal(id="unrelated", chat_id=chat.id, objective="Other work"))
  db.add(models.ChatRun(id="unrelated-run", chat_id=chat.id, goal_id="unrelated", status="completed"))
  db.commit()
  scheduled = []
  monkeypatch.setattr(chat_mod, "_schedule_continuation", lambda **kw: scheduled.append(kw))
  assert asyncio.run(deps.deliver_dependency(row.id))
  assert not asyncio.run(deps.deliver_dependency(row.id))
  assert len(scheduled) == 1
  db.expire_all()
  receiver = db.get(models.ChatRun, "dependency-resume-" + row.id)
  assert receiver.goal_id == "dependent-goal" and row.received_run_id is None
  get_writer().submit(AdmitProviderExecution(chat_id=chat.id, run_token=receiver.id,
    dependency_results=(row.id,))).result(5)
  get_writer().submit(AcknowledgeProviderSuccess(chat_id=chat.id, run_token=receiver.id,
    dependency_results=(row.id,))).result(5)
  db.expire_all()
  assert row.received_run_id == receiver.id


def test_already_answered_card_is_immediately_owed(db, chat):
  _setup(db, chat)
  _answer()
  row = _follow(db, chat)
  assert row.outcome_json["kind"] == "answered" and row.resolved_at


def test_quiet_close_holds_source_but_owes_actual_answer_to_dependent(db, chat):
  _setup(db, chat, quiet=True)
  row = _follow(db, chat)
  _answer(quiet=True)
  db.expire_all()
  assert row.outcome_json["kind"] == "closed_without_reply"
  assert row.outcome_json["answers"] == {"q": "Not now"}
  assert db.get(models.ChatGoal, "source-goal").status == "stopped"
  assert not db.get(models.Chat, "source").pending_messages
  assert db.get(models.ChatGoal, "dependent-goal").status == "open"


def test_stop_cancellation_survives_process_registry_loss(db, chat):
  _setup(db, chat)
  row = _follow(db, chat)
  get_writer().submit(FinishRun(chat_id="source", terminal_status="stopped")).result(5)
  questions._cancelled.clear()
  db.expire_all()
  assert row.outcome_json["kind"] == "cancelled"
  assert saved_decision(db, "source", "choice").outcome_json["kind"] == "cancelled"


def test_replacement_stamps_original_without_following_new_card(db, chat):
  _source, card = _setup(db, chat)
  row = _follow(db, chat)
  card = dict(card, question_id="replacement")
  get_writer().submit(QuestionCommit(chat_id="source", run_token="source-run",
    snapshot={"id": "source-run", "role": "assistant", "blocks": [card], "ts": 2})).result(5)
  db.expire_all()
  assert row.outcome_json["kind"] == "superseded"
  assert db.get(models.Chat, "source").pending_question_id == "replacement"


@pytest.mark.parametrize("answered", [False, True])
def test_source_deletion_preserves_answer_or_records_deleted_atomically(db, chat, answered):
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  if answered:
    _answer()
  db.expire_all()
  stage_delete_decisions(db, source)
  source.deleted_at = now_naive_utc()
  db.commit()
  db.expire_all()
  assert row.outcome_json["kind"] == ("answered" if answered else "source_deleted")


@pytest.mark.parametrize("family", ["restart", "sealed"])
def test_restart_and_sealed_input_are_not_followable(db, chat, family):
  _setup(db, chat, family=family)
  with pytest.raises(ValueError, match="Restart and sealed"):
    _follow(db, chat)
  assert db.query(models.SavedDecisionCard).count() == 0
  assert db.query(models.GoalDependency).count() == 0


def test_approval_result_is_evidence_not_executor_authority(db, chat):
  _setup(db, chat, family="approval")
  row = _follow(db, chat)
  _answer()
  db.expire_all()
  assert row.outcome_json["family"] == "approval"
  assert "not permission" in deps.dependency_notice(row)
  assert "action_key" not in row.outcome_json
  assert db.query(models.AgentWorkClaim).count() == 0


def test_held_dependent_keeps_answer_and_deletion_retires_without_receipt(db, chat):
  _setup(db, chat)
  row = _follow(db, chat)
  db.get(models.ChatGoal, "dependent-goal").status = "stopped"
  db.commit()
  _answer()
  db.expire_all()
  assert not asyncio.run(deps.deliver_dependency(row.id))
  assert row.outcome_json["kind"] == "answered" and row.received_run_id is None
  deps.stage_retire_chat_dependencies(db, chat.id)
  db.commit()
  db.expire_all()
  assert row.result_disposition == "withdrawn"
  assert row.received_run_id is None


@pytest.mark.parametrize("order", ["answer_first", "subscribe_first"])
def test_answer_and_subscription_serialize_without_losing_outcome(db, chat, monkeypatch, order):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Event
  from app import decision_cards
  from app.database import SessionLocal
  _setup(db, chat)
  chat_id = chat.id
  db.rollback()
  entered, release = Event(), Event()
  original = decision_cards.fence_decisions
  first = [True]

  def fenced(session):
    original(session)
    if first[0]:
      first[0] = False
      entered.set()
      assert release.wait(5)
  monkeypatch.setattr(decision_cards, "fence_decisions", fenced)

  def follow():
    with SessionLocal() as session:
      row = stage_wait_for_decision(session, chat_id=chat_id, run_id="dependent-run",
        source_chat_id="source", question_id="choice")
      session.commit()
      return row.id

  with ThreadPoolExecutor(max_workers=2) as pool:
    leading = pool.submit(_answer if order == "answer_first" else follow)
    assert entered.wait(5)
    trailing = pool.submit(follow if order == "answer_first" else _answer)
    release.set()
    results = [leading.result(10), trailing.result(10)]
  db.expire_all()
  row = db.get(models.GoalDependency, next(value for value in results if value))
  assert row.outcome_json["kind"] == "answered"
  assert row.outcome_json["selected_options"] == {"q": ["later"]}
  assert db.query(models.GoalDependency).count() == 1


def test_direct_card_cycle_and_self_card_are_refused(db, chat):
  source, _ = _setup(db, chat)
  # Source's saved card has exact Goal ownership from its assistant run.
  own = {"type": "question", "question_id": "own", "questions": [{"question": "Continue?"}]}
  from app.decision_cards import stage_saved_decision
  stage_saved_decision(db, chat, own, source_goal_id="dependent-goal")
  db.add(models.GoalDependency(id="reverse", waiting_goal_id="source-goal",
    waiting_chat_id="source", target_kind="owner_card", target_key="card:own",
    target_chat_id=chat.id, target_question_id="own"))
  db.commit()
  with pytest.raises(ValueError, match="cycle"):
    _follow(db, chat)
  with pytest.raises(ValueError, match="own chat"):
    stage_wait_for_decision(db, chat_id=chat.id, run_id="dependent-run",
      source_chat_id=chat.id, question_id="own")


def test_deleted_source_history_keeps_outcome_without_implying_receipt(db, chat):
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  db.expire_all()
  stage_delete_decisions(db, source)
  source.deleted_at = now_naive_utc()
  deps.stage_retire_goal_dependencies(db, chat.id, "dependent-goal")
  db.commit()
  history = deps.goal_dependency_history(db, db.get(models.ChatGoal, "dependent-goal"))
  assert history[0]["source_deleted"] and history[0]["received_run_id"] is None
  assert history[0]["outcome"]["kind"] == "answered"
  assert history[0]["disposition"] == "retired_by_goal_outcome"


def test_saved_decision_schema_upgrade_is_repeatable_without_history_adoption(tmp_path):
  from sqlalchemy import create_engine, inspect, text
  from app.schema_migrations import _add_saved_decision_cards
  engine = create_engine(f"sqlite:///{tmp_path / 'cards.db'}")
  models.Base.metadata.create_all(engine)
  with engine.begin() as conn:
    conn.execute(text("DROP TABLE saved_decision_cards"))
  _add_saved_decision_cards(engine)
  _add_saved_decision_cards(engine)
  assert "saved_decision_cards" in inspect(engine).get_table_names()
  with engine.connect() as conn:
    assert conn.execute(text("SELECT count(*) FROM saved_decision_cards")).scalar_one() == 0


@pytest.mark.parametrize("invalid", ["goalless", "held", "helper", "wrong_card"])
def test_only_open_top_level_goal_and_exact_saved_card_can_subscribe(db, chat, invalid):
  _setup(db, chat)
  if invalid == "goalless":
    db.get(models.ChatRun, "dependent-run").goal_id = None
  elif invalid == "held":
    db.get(models.ChatGoal, "dependent-goal").status = "stopped"
  elif invalid == "helper":
    from tests.test_chat_waits import _delegated_agent_run_auth
    _delegated_agent_run_auth(db, chat.id, "dependent-run")
  db.commit()
  with pytest.raises(ValueError, match="saved decision" if invalid == "wrong_card" else "top-level"):
    _follow(db, chat, "missing" if invalid == "wrong_card" else "choice")
  assert db.query(models.GoalDependency).count() == 0


def test_run_bound_wait_for_decision_route_and_auth(db, chat, client, auth):
  from tests.test_chat_waits import _agent_run_auth
  _setup(db, chat)
  db.get(models.ChatRun, "dependent-run").status = "running"
  db.commit()
  headers = _agent_run_auth(db, chat.id, "dependent-run")
  payload = {"chat_id": "source", "question_id": "choice"}
  assert client.post("/api/chat-waits/decision", json=payload, headers=auth).status_code == 403
  response = client.post("/api/chat-waits/decision", json=payload, headers=headers)
  assert response.status_code == 200, response.text
  assert response.json()["status"] == "waiting"
  assert response.json()["goal_id"] == "dependent-goal"
  assert "only to the card's own executor" in response.json()["next_action"]
  deps.stage_retire_chat_dependencies(db, chat.id)
  db.commit()
  repeated = client.post("/api/chat-waits/decision", json=payload, headers=headers)
  assert repeated.json()["status"] == "withdrawn"
  assert "no new continuation" in repeated.json()["next_action"]


def test_exact_answered_card_navigation_loads_older_card_not_new_pending_card(db, chat, client, auth):
  from app import transcript_rows
  source, _ = _setup(db, chat)
  _answer()
  db.expire_all()
  messages = list(transcript_rows.history(source))
  messages.extend({"role": "user", "content": str(i), "ts": 100 + i} for i in range(30))
  messages.append({"role": "assistant", "id": "later", "ts": 999, "blocks": [
    {"type": "question", "question_id": "later-card", "questions": [{"question": "Another choice?"}]}]})
  transcript_rows.replace_all(db, source, messages)
  source.pending_question_id = "later-card"
  db.commit()
  response = client.get("/api/chats/source?anchor=question:choice&compact=true&limit=2", headers=auth)
  assert response.status_code == 200, response.text
  data = response.json()
  assert data["requested_anchor_found"] is True
  cards = [block for message in data["messages"] for block in message.get("blocks", []) if block.get("type") == "question"]
  assert cards[0]["question_id"] == "choice" and cards[0]["answers"] == {"q": "Not now"}
  assert data["pending_question_id"] == "later-card"
  assert db.query(models.ChatRun).filter_by(chat_id="source").count() == 1


@pytest.mark.parametrize("target", ["source", "dependent"])
def test_real_delete_route_stamps_source_or_retires_dependent(db, chat, client, auth, target):
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  target_id = source.id if target == "source" else chat.id
  response = client.delete(f"/api/chats/{target_id}", headers=auth)
  assert response.status_code == 204, response.text
  db.expire_all()
  if target == "source":
    assert row.outcome_json["kind"] == "source_deleted"
  else:
    assert row.result_disposition == "withdrawn" and row.received_run_id is None
    assert source.deleted_at is None and source.pending_question_id == "choice"


def test_later_explicit_answer_to_stopped_card_does_not_rewrite_owed_cancellation(db, chat):
  _setup(db, chat)
  row = _follow(db, chat)
  get_writer().submit(FinishRun(chat_id="source", terminal_status="stopped")).result(5)
  _answer()
  db.expire_all()
  assert row.outcome_json["kind"] == "cancelled"
  assert saved_decision(db, "source", "choice").outcome_json["kind"] == "answered"


def test_source_purge_drops_card_record_but_retains_dependent_answer(db, chat, monkeypatch):
  from datetime import timedelta
  from app import chat_retention
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  db.expire_all()
  stage_delete_decisions(db, source)
  source.deleted_at = now_naive_utc() - timedelta(days=8)
  db.get(models.ChatRun, "source-run").status = "completed"
  db.commit()
  monkeypatch.setattr(chat_retention, "_purge_chat_storage", lambda _: None)
  chat_retention.purge_expired_chat_tombstones(db)
  db.expire_all()
  assert db.get(models.Chat, "source") is None
  assert saved_decision(db, "source", "choice") is None
  assert row.outcome_json["kind"] == "answered" and row.outcome_json["answers"] == {"q": "Not now"}


def test_claim_follow_cannot_close_reverse_edge_through_a_saved_card(db, chat):
  from app.agent_work_claims import claim_work
  _setup(db, chat)
  _follow(db, chat)
  owner = db.query(models.Owner).first()
  claim_work(db, owner_id=owner.id, chat_id=chat.id, run_id="dependent-run",
    work_key="exact:action", summary="Own the action")
  response = claim_work(db, owner_id=owner.id, chat_id="source", run_id="source-run",
    work_key="exact:action", summary="Follow action")
  assert response["follow_status"] == "direct_cycle"
  assert db.query(models.GoalDependency).count() == 1


def test_answer_commit_failure_rolls_back_source_disposition_and_dependent_outcome(db, chat, monkeypatch):
  from app import chat_writer
  _setup(db, chat)
  row = _follow(db, chat)
  def fail_commit(session):
    session.rollback()
    return False
  monkeypatch.setattr(chat_writer, "_commit_or_rollback", fail_commit)
  with pytest.raises(chat_writer._PersistFailed, match="AnswerQuestion did not persist"):
    _answer()
  db.expire_all()
  assert row.resolved_at is None and row.outcome_json is None
  assert saved_decision(db, "source", "choice").outcome_json is None
  assert db.get(models.Chat, "source").pending_question_id == "choice"


@pytest.mark.parametrize("already_following", [False, True])
def test_preobserved_orm_state_cannot_hide_an_answer_committed_before_subscribe(db, chat, already_following):
  _setup(db, chat)
  stale_card = saved_decision(db, "source", "choice")
  assert stale_card.outcome_json is None
  previous = _follow(db, chat) if already_following else None
  assert stale_card.outcome_json is None
  if previous is not None:
    assert previous.outcome_json is None
  _answer()
  # Deliberately retain the caller's pre-answer identity map, like a route
  # that inspected the card before waiting on the registration graph fence.
  assert stale_card.outcome_json is None
  row = _follow(db, chat)
  assert row.outcome_json["kind"] == "answered"
  if previous:
    assert row.id == previous.id


@pytest.mark.parametrize("target", ["source", "dependent"])
def test_project_delete_and_restore_do_not_lose_or_revive_card_dependencies(db, chat, client, auth, target, monkeypatch):
  monkeypatch.setattr(chat_mod, "_schedule_continuation", lambda **_kwargs: None)
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  project = client.post("/api/projects", headers=auth,
    json={"name": "Decision project", "template_id": "blank"}).json()
  (source if target == "source" else chat).project_id = project["id"]
  db.commit()
  response = client.delete(f"/api/projects/{project['id']}", headers=auth)
  assert response.status_code == 204, response.text
  db.expire_all()
  if target == "source":
    assert row.outcome_json["kind"] == "source_deleted"
  else:
    assert row.result_disposition == "withdrawn" and row.received_run_id is None
  # Recovery cannot erase the historical source disposition or revive a
  # dependent subscription that deletion already retired.
  from app import transcript_rows
  before = dict(row.outcome_json or {})
  if target == "source":
    # Deleting the source legitimately owes source_deleted to its follower.
    # Receive that first delivery; restoration must not revive it. An
    # unadmitted carrier is correctly retryable and is not a receipt.
    asyncio.run(deps.deliver_dependency(row.id))
    db.expire_all()
    receiver = db.get(models.ChatRun, "dependency-resume-" + row.id)
    get_writer().submit(AdmitProviderExecution(chat_id=chat.id, run_token=receiver.id,
      dependency_results=(row.id,))).result(5)
    get_writer().submit(AcknowledgeProviderSuccess(chat_id=chat.id, run_token=receiver.id,
      dependency_results=(row.id,))).result(5)
    db.expire_all()
    assert row.result_disposition == "delivered"
  delivered_before = [m.get("cid") for m in transcript_rows.history(chat)
    if m.get("kind") == "dependency_result"]
  assert len(delivered_before) == (1 if target == "source" else 0)
  receipt = response.headers["X-Recovery-Notification-Id"]
  recovered = client.post(f"/api/projects/{project['id']}/recover", headers=auth,
    json={"notification_id": receipt})
  assert recovered.status_code == 200, recovered.text
  db.expire_all()
  assert not asyncio.run(deps.deliver_dependency(row.id))
  db.expire_all()
  assert [m.get("cid") for m in transcript_rows.history(chat)
    if m.get("kind") == "dependency_result"] == delivered_before
  assert row.outcome_json == before if target == "source" else row.withdrawn_at is not None


@pytest.mark.parametrize("replacement", ["removed", "new_card", "answered", "preserved"])
def test_transcript_put_settles_removed_cards_and_adopts_replacements(db, chat, client, auth, replacement):
  source, card = _setup(db, chat)
  row = _follow(db, chat)
  incoming = dict(card)
  if replacement == "new_card":
    incoming["question_id"] = "new-choice"
  elif replacement == "answered":
    incoming.update(answers={"q": "Build and test"}, selected_options={"q": ["yes"]})
  messages = [] if replacement == "removed" else [
    {"id": "source-run", "role": "assistant", "blocks": [incoming], "ts": 2}]
  result = client.put("/api/chats/source", headers=auth, json={"messages": messages})
  assert result.status_code == 200, result.text
  db.expire_all()
  if replacement == "preserved":
    assert row.outcome_json is None and source.pending_question_id == "choice"
  else:
    assert row.outcome_json["kind"] == ("answered" if replacement == "answered" else "superseded")
    assert row.resolved_at is not None
  if replacement == "new_card":
    new = _follow(db, chat, "new-choice")
    assert new.outcome_json is None and new.id != row.id
  elif replacement == "answered":
    assert row.outcome_json["selected_options"] == {"q": ["yes"]}
  elif replacement == "removed":
    # A later subscription observes the durable removal, never an endless wait.
    assert _follow(db, chat).outcome_json["kind"] == "superseded"


def test_transcript_removal_preserves_a_previously_answered_card(db, chat, client, auth):
  _setup(db, chat)
  _answer()
  result = client.put("/api/chats/source", headers=auth, json={"messages": []})
  assert result.status_code == 200
  row = _follow(db, chat)
  assert row.outcome_json["kind"] == "answered"
  assert row.outcome_json["answers"] == {"q": "Not now"}


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("terminal", ["restart", "stopped", "completed"])
def test_finalization_does_not_preempt_native_or_continuation_card_lifecycle(db, chat, native, terminal):
  from app.chat_writer import Finalize, ParkRun
  source, card = _setup(db, chat)
  if native:
    card.pop("response_mode")
    get_writer().submit(QuestionCommit(chat_id="source", run_token="source-run",
      snapshot={"id": "source-run", "role": "assistant", "blocks": [card], "ts": 2})).result(5)
  row = _follow(db, chat)
  get_writer().submit(Finalize(chat_id="source", run_token="source-run",
    snapshot={"id": "source-run", "role": "assistant", "blocks": [card], "ts": 2})).result(5)
  db.expire_all()
  assert row.outcome_json is None
  if terminal == "restart":
    assert get_writer().submit(ParkRun(chat_id="source", run_token="source-run",
      park_reason="restart", restart_nonce="test", parked_until=now_naive_utc())).result(5)
    db.expire_all()
    assert source.pending_question_id == "choice" and row.outcome_json is None
    _answer()
    db.expire_all()
    assert row.outcome_json["kind"] == "answered"
  else:
    get_writer().submit(FinishRun(chat_id="source", run_token="source-run", terminal_status=terminal)).result(5)
    db.expire_all()
    if terminal == "stopped":
      assert row.outcome_json["kind"] == "cancelled"
    elif native:
      assert row.outcome_json["kind"] == "superseded"
    else:
      assert row.outcome_json is None and source.pending_question_id == "choice"


def test_transcript_replacement_rollback_keeps_source_and_dependency_open(db, chat, monkeypatch):
  from app import chat_writer
  _setup(db, chat)
  row = _follow(db, chat)
  def fail_commit(session):
    session.rollback()
    return False
  monkeypatch.setattr(chat_writer, "_commit_or_rollback", fail_commit)
  with pytest.raises(chat_writer._PersistFailed, match="ReplaceTranscript did not persist"):
    get_writer().submit(chat_writer.ReplaceTranscript(chat_id="source", messages=[])).result(5)
  db.expire_all()
  assert row.outcome_json is None
  assert saved_decision(db, "source", "choice").outcome_json is None
  assert questions.saved_question(db.get(models.Chat, "source"), "choice") is not None


@pytest.mark.parametrize("order", ["replace_first", "subscribe_first"])
def test_transcript_replacement_and_subscription_share_atomic_disposition(db, chat, monkeypatch, order):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Event
  from app import decision_cards
  from app.database import SessionLocal
  from app.chat_writer import ReplaceTranscript
  _setup(db, chat)
  chat_id = chat.id
  db.rollback()
  entered, release = Event(), Event()
  original = decision_cards.fence_decisions
  first = [True]
  def fenced(session):
    original(session)
    if first[0]:
      first[0] = False
      entered.set()
      assert release.wait(5)
  monkeypatch.setattr(decision_cards, "fence_decisions", fenced)
  def follow():
    with SessionLocal() as session:
      row = stage_wait_for_decision(session, chat_id=chat_id, run_id="dependent-run",
        source_chat_id="source", question_id="choice")
      session.commit()
      return row.id
  def replace():
    get_writer().submit(ReplaceTranscript(chat_id="source", messages=[])).result(5)
  with ThreadPoolExecutor(max_workers=2) as pool:
    leading = pool.submit(replace if order == "replace_first" else follow)
    assert entered.wait(5)
    trailing = pool.submit(follow if order == "replace_first" else replace)
    release.set()
    results = [leading.result(10), trailing.result(10)]
  db.expire_all()
  row = db.get(models.GoalDependency, next(value for value in results if value))
  assert row.outcome_json["kind"] == "superseded"
  assert db.query(models.GoalDependency).count() == 1


def test_replacing_a_legacy_open_card_preserves_its_exact_goal_owner(db, chat, client, auth):
  _source, card = _setup(db, chat)
  db.delete(saved_decision(db, "source", "choice"))
  db.commit()
  result = client.put("/api/chats/source", headers=auth, json={"messages": [
    {"id": "source-run", "role": "assistant", "blocks": [card], "ts": 2}]})
  assert result.status_code == 200
  assert saved_decision(db, "source", "choice").source_goal_id == "source-goal"


def test_foreign_not_now_can_defer_receipt_and_resume_without_redelivery(db, chat, monkeypatch):
  from app import transcript_rows
  from app.broadcast import create_broadcast, remove_broadcast
  from app.chat_event_sink import ChatEventSink
  from app.goals import update_goal_record
  from tests.test_goal_hold_lifecycle import resume

  _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  scheduled = []
  monkeypatch.setattr(chat_mod, "_schedule_continuation", lambda **kw: scheduled.append(kw))
  monkeypatch.setattr(chat_mod, "_publish_chat_run_finished", lambda *_: None)
  assert asyncio.run(deps.deliver_dependency(row.id))
  db.expire_all()
  goal = db.get(models.ChatGoal, "dependent-goal")
  receiver = db.get(models.ChatRun, "dependency-resume-" + row.id)
  assert row.outcome_json["answers"] == {"q": "Not now"}
  assert deps.owed_dependency_results(db, chat.id, "", run_id=receiver.id)[1] == (row.id,)
  get_writer().submit(AdmitProviderExecution(chat_id=chat.id, run_token=receiver.id,
    dependency_results=(row.id,))).result(5)
  db.expire_all()
  update_goal_record(db, receiver, goal, goal.revision,
    defer="The owner chose Not now in the source decision; remaining work is on hold.")
  get_writer().submit(AcknowledgeProviderSuccess(chat_id=chat.id, run_token=receiver.id,
    dependency_results=(row.id,))).result(5)
  receiver_id, row_id, chat_id = receiver.id, row.id, chat.id
  bc = create_broadcast(chat_id)
  sink = ChatEventSink(bc, chat_id, run_token=receiver_id)
  sink.publish({"type": "text", "content": "Work is on hold as requested."})
  try:
    asyncio.run(chat_mod._complete_turn(bc=bc, sink=sink, db=db, chat_id=chat_id,
      run_gen=None, provider_id="claude", cost_usd=0, close_browser=False))
  finally:
    remove_broadcast(chat_id)
  db.expire_all()
  goal, row = db.get(models.ChatGoal, "dependent-goal"), db.get(models.GoalDependency, row_id)
  saved = db.get(models.Chat, chat_id)
  assert goal.status == "stopped" and row.received_run_id == receiver_id
  assert row.result_disposition == "delivered"
  assert len(scheduled) == 1
  assert not any(b.get("type") == "error" for m in transcript_rows.history(saved) for b in m.get("blocks", []))
  get_writer().submit(resume(saved, goal, token="owner-resumed")).result(5)
  db.expire_all()
  assert goal.status == "open"
  assert deps.owed_dependency_results(db, chat_id, "", run_id="owner-resumed") == ("", ())
  assert not asyncio.run(deps.deliver_dependency(row_id))
  assert len(scheduled) == 1


@pytest.mark.parametrize("outcome", ["complete", "defer"])
@pytest.mark.parametrize("already_following", [False, True])
def test_already_answered_follow_can_finish_same_turn_without_redelivery(
    db, chat, monkeypatch, outcome, already_following):
  from app.goals import update_goal_record
  from tests.test_goal_hold_lifecycle import resume

  _setup(db, chat)
  if already_following:
    _follow(db, chat)
  _answer()
  run = db.get(models.ChatRun, "dependent-run")
  run.status = "running"
  db.commit()
  get_writer().submit(AdmitProviderExecution(
    chat_id=chat.id, run_token=run.id)).result(5)
  # The result was not in the admission-time prompt. The explicit tool call
  # now returns the settled decision to this already-entered exact-Goal run.
  row = _follow(db, chat)
  assert row.outcome_json["answers"] == {"q": "Not now"}
  db.expire_all()
  assert run.admitted_dependency_results_json == [row.id]
  assert row.received_run_id is None
  assert _follow(db, chat).id == row.id
  assert run.admitted_dependency_results_json == [row.id]
  goal = db.get(models.ChatGoal, "dependent-goal")
  update_goal_record(db, run, goal, goal.revision, **(
    {"complete": True} if outcome == "complete" else
    {"defer": "The source owner chose Not now; no other authorized work remains."}))
  # Runtime ACK still has the empty original prompt selection. It must also
  # receipt evidence returned by tools during this physical run.
  get_writer().submit(AcknowledgeProviderSuccess(
    chat_id=chat.id, run_token=run.id)).result(5)
  db.expire_all()
  if outcome == "defer":
    assert goal.status == "stopped"
    assert row.result_disposition == "delivered"
    assert row.received_run_id == run.id
    run.status = "completed"
    db.commit()
    get_writer().submit(resume(chat, goal, token="owner-resumed")).result(5)
    db.expire_all()
    assert deps.owed_dependency_results(db, chat.id, "", run_id="owner-resumed") == ("", ())
  else:
    assert goal.status == "completed"
    assert row.result_disposition == "retired_by_goal_outcome"
  scheduled = []
  monkeypatch.setattr(chat_mod, "_schedule_continuation", lambda **kw: scheduled.append(kw))
  assert not asyncio.run(deps.deliver_dependency(row.id))
  assert scheduled == []


def test_settled_follow_does_not_acknowledge_before_provider_success(db, chat):
  _setup(db, chat)
  _answer()
  run = db.get(models.ChatRun, "dependent-run")
  run.status = "running"
  db.commit()
  get_writer().submit(AdmitProviderExecution(chat_id=chat.id, run_token=run.id)).result(5)
  row = _follow(db, chat)
  # A failed provider call must leave the returned evidence owed to the same
  # Goal, rather than recording a delivery solely from the tool transaction.
  run.status = "failed"
  db.add(models.ChatRun(id="retry", chat_id=chat.id, goal_id=run.goal_id, status="running"))
  db.commit()
  assert deps.owed_dependency_results(db, chat.id, "", run_id="retry")[1] == (row.id,)
  assert row.received_run_id is None and row.result_disposition is None


def test_card_follow_cap_counts_claims_but_not_owner_suppressed_rows(db, chat):
  from app.goal_dependency_sources import outstanding_dependency_count
  _setup(db, chat)
  for index in range(65):
    db.add(models.GoalDependency(id=f"claim-{index}", waiting_chat_id=chat.id,
      waiting_goal_id="dependent-goal", target_kind="claim_acquisition",
      target_key=f"claim:acquisition-{index}", target_acquisition_id=f"acquisition-{index}",
      owner_suppressed_at=now_naive_utc() if index == 0 else None))
  db.commit()
  assert outstanding_dependency_count(db, "dependent-goal") == 64
  with pytest.raises(ValueError, match="64 outstanding"):
    _follow(db, chat)
  db.get(models.GoalDependency, "claim-1").result_disposition = "delivered"
  db.commit()
  assert _follow(db, chat).target_kind == "owner_card"
  assert outstanding_dependency_count(db, "dependent-goal") == 64
