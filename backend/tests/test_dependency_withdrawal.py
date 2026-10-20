"""Exact owner withdrawal is serialized with resolution and provider admission."""
import asyncio

import pytest

from app import models, goal_dependencies as deps
from app.chat_handoffs import goal_handoffs
from app.chat_writer import (
  WithdrawGoalDependency, AdmitProviderExecution, AcknowledgeProviderSuccess,
  AppendPending, _PersistFailed, get_writer,
)
from app.goal_plans import serialize_goal
from tests.test_decision_dependencies import _setup, _follow, _answer


def _withdraw(chat_id, dependency_id):
  return get_writer().submit(WithdrawGoalDependency(
    chat_id=chat_id, dependency_id=dependency_id)).result(5)


@pytest.mark.parametrize('held', [False, True])
@pytest.mark.parametrize('resolved', [False, True])
def test_owner_route_withdraws_one_edge_and_keeps_source_goal_and_saved_outcome(db, chat, client, auth, held, resolved):
  source, _ = _setup(db, chat)
  row = _follow(db, chat)
  # Another Goal and another edge must not be retired along with this one.
  db.add(models.ChatGoal(id='other-goal', chat_id=chat.id, objective='Other work'))
  db.add(models.GoalDependency(id='other-edge', waiting_goal_id='other-goal',
    waiting_chat_id=chat.id, target_kind='owner_card', target_key=row.target_key,
    target_chat_id=source.id, target_question_id='choice'))
  goal = db.get(models.ChatGoal, row.waiting_goal_id)
  goal.status = 'stopped' if held else 'open'
  db.commit()
  if resolved:
    _answer()
  db.expire_all()
  old_outcome = row.outcome_json
  source_state = (source.pending_question_id, list(source.pending_messages or []))
  path = f'/api/chat-waits/dependencies/{row.id}/withdraw'
  response = client.post(path, headers=auth)
  assert response.status_code == 200, response.text
  assert response.json()['disposition'] == 'withdrawn'
  db.expire_all()
  when = row.withdrawn_at
  assert when and row.received_run_id is None
  if resolved:
    assert row.outcome_json == old_outcome
  else:
    assert row.outcome_json['summary'] == 'Which release?'
  assert (source.pending_question_id, list(source.pending_messages or [])) == source_state
  assert goal.status == ('stopped' if held else 'open')
  assert db.get(models.GoalDependency, 'other-edge').result_disposition is None
  assert client.post(path, headers=auth).status_code == 200
  db.expire_all()
  assert row.withdrawn_at == when
  assert not goal_handoffs(db, goal).prevents_settlement
  presentation = serialize_goal(db, db.get(models.ChatRun, 'dependent-run'), goal)
  assert presentation['resumable'] and presentation['status'] == 'paused'
  assert presentation['dependency_history'][0]['disposition'] == 'withdrawn'
  assert not asyncio.run(deps.deliver_dependency(row.id))
  if not held:
    assert _follow(db, chat).result_disposition == 'withdrawn'


@pytest.mark.parametrize('answer_first', [False, True])
def test_withdrawal_and_answer_writer_order_preserves_only_already_stamped_outcome(db, chat, answer_first):
  _setup(db, chat)
  row = _follow(db, chat)
  if answer_first:
    _answer()
  assert _withdraw(chat.id, row.id) == 'withdrawn'
  if not answer_first:
    _answer()
  db.expire_all()
  assert row.result_disposition == 'withdrawn' and row.received_run_id is None
  assert row.outcome_json.get('kind') == ('answered' if answer_first else None)
  assert not asyncio.run(deps.deliver_dependency(row.id))


def test_withdrawal_retires_only_its_queued_notice_and_rejects_a_stale_enqueue(db, chat):
  _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  message = {'role': 'user', 'content': 'result', 'cid': 'dependency-result-' + row.id,
    'source_work_id': 'dependent-run', 'kind': 'goal_dependency_result', 'hidden': True}
  from app.continuations import DEPENDENCY_RESULT_MESSAGE_KIND
  message['kind'] = DEPENDENCY_RESULT_MESSAGE_KIND
  get_writer().submit(AppendPending(chat_id=chat.id, user_msg=message)).result(5)
  get_writer().submit(AppendPending(chat_id=chat.id, user_msg={
    'role': 'user', 'content': 'Unrelated owner message', 'cid': 'owner'})).result(5)
  assert _withdraw(chat.id, row.id) == 'withdrawn'
  db.expire_all()
  assert [m['cid'] for m in chat.pending_messages] == ['owner']
  with pytest.raises(_PersistFailed, match='no longer receivable'):
    get_writer().submit(AppendPending(chat_id=chat.id, user_msg=message)).result(5)
  db.expire_all()
  assert [m['cid'] for m in chat.pending_messages] == ['owner']


@pytest.mark.parametrize('admitted_first', [False, True])
def test_withdrawal_vs_admission_invalidates_only_unentered_execution(db, chat, monkeypatch, admitted_first):
  from app import chat as chat_mod
  _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  monkeypatch.setattr(chat_mod, '_schedule_continuation', lambda **kw: None)
  assert asyncio.run(deps.deliver_dependency(row.id))
  db.expire_all()
  carrier = db.get(models.ChatRun, 'dependency-resume-' + row.id)
  command = AdmitProviderExecution(chat_id=chat.id, run_token=carrier.id, dependency_results=(row.id,))
  if admitted_first:
    get_writer().submit(command).result(5)
  assert _withdraw(chat.id, row.id) == 'withdrawn'
  if not admitted_first:
    db.expire_all()
    assert chat.live_assistant is None and chat.active_assistant_message_id is None
    with pytest.raises(_PersistFailed):
      get_writer().submit(command).result(5)
  else:
    # A real prompt cannot be unread. Withdrawal never stops this execution,
    # but an acknowledgement cannot turn the withdrawn edge into a receipt.
    get_writer().submit(AcknowledgeProviderSuccess(chat_id=chat.id,
      run_token=carrier.id, dependency_results=(row.id,))).result(5)
  db.expire_all()
  assert carrier.status == ('running' if admitted_first else 'interrupted')
  assert row.result_disposition == 'withdrawn' and row.received_run_id is None
  assert row.outcome_json['kind'] == 'answered'
  assert db.get(models.ChatGoal, row.waiting_goal_id).status == 'open'
  assert not asyncio.run(deps.deliver_dependency(row.id))


def test_chat_admission_reselects_after_selected_dependency_is_withdrawn(db, chat):
  from app import chat as chat_mod
  _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  db.expire_all()
  run = db.get(models.ChatRun, "dependent-run")
  run.status = "running"
  run.provider_execution_admitted = False
  db.commit()
  selected, wait_ids, dependency_ids = chat_mod._selected_result_context(
    db, chat.id, run.id, "Continue work",
  )
  assert dependency_ids == (row.id,) and row.id in selected
  assert _withdraw(chat.id, row.id) == "withdrawn"
  row_id, run_id = row.id, run.id
  admitted, prompt, delivered_waits, delivered_dependencies = asyncio.run(
    chat_mod._admit_with_selected_results(
      db, chat.id, run.id, None, raw_message="Continue work",
      user_message="before <results> Continue work", context_marker="<results>",
      selected_context=selected, wait_results=wait_ids,
      dependency_results=dependency_ids, has_peer_context_delivery=False,
      activity_results=(),
    )
  )
  assert admitted and "Continue work" in prompt
  assert row_id not in prompt
  assert delivered_waits == delivered_dependencies == ()
  db.expire_all()
  assert db.get(models.ChatRun, run_id).provider_execution_admitted


@pytest.mark.parametrize('terminal', ['completed', 'cancelled', 'cannot_complete', 'dismissed'])
def test_closed_goals_cannot_acquire_a_new_withdrawal_disposition(db, chat, client, auth, terminal):
  _setup(db, chat)
  row = _follow(db, chat)
  db.get(models.ChatGoal, row.waiting_goal_id).status = terminal
  db.commit()
  assert client.post(f'/api/chat-waits/dependencies/{row.id}/withdraw', headers=auth).status_code == 409
  db.expire_all()
  assert row.result_disposition is None


def test_delivered_result_is_not_rewritten_as_withdrawn(db, chat, client, auth, monkeypatch):
  from app import chat as chat_mod
  _setup(db, chat)
  row = _follow(db, chat)
  _answer()
  monkeypatch.setattr(chat_mod, '_schedule_continuation', lambda **kw: None)
  assert asyncio.run(deps.deliver_dependency(row.id))
  token = 'dependency-resume-' + row.id
  get_writer().submit(AdmitProviderExecution(chat_id=chat.id, run_token=token,
    dependency_results=(row.id,))).result(5)
  get_writer().submit(AcknowledgeProviderSuccess(chat_id=chat.id, run_token=token,
    dependency_results=(row.id,))).result(5)
  assert client.post(f'/api/chat-waits/dependencies/{row.id}/withdraw', headers=auth).status_code == 409
  db.expire_all()
  assert row.result_disposition == 'delivered' and row.received_run_id == token


def test_withdrawal_auth_is_bound_to_waiting_chat_not_source_or_embed(db, chat, client, auth):
  from app.auth import create_access_token
  from tests.test_chat_waits import _agent_run_auth
  _setup(db, chat)
  row = _follow(db, chat)
  path = f'/api/chat-waits/dependencies/{row.id}/withdraw'
  assert client.post(path).status_code == 401
  for scope in ('app', 'chat_embed'):
    token = create_access_token({'sub': 'owner', 'scope': scope, 'chat_id': chat.id, 'app_id': 'test-app'})
    assert client.post(path, headers={'Authorization': f'Bearer {token}'}).status_code in {401, 403}
  source_auth = _agent_run_auth(db, 'source', 'source-run')
  assert client.post(path, headers=source_auth).status_code == 403
  assert _withdraw('source', row.id) == 'missing'
  assert client.post('/api/chat-waits/dependencies/missing/withdraw', headers=auth).status_code == 404


def test_withdrawn_claim_cannot_revive_on_collision_or_transfer(db):
  from tests.test_agent_work_claims import _fixture
  from app.agent_work_claims import claim_work
  owner, first, second = _fixture(db)
  def claim(chat, run, **kw):
    return claim_work(db, owner_id=owner.id, chat_id=chat.id, run_id=run,
      work_key='repair', summary='Repair host', **kw)
  claim(first, 'claim-run-first')
  claim(second, 'claim-run-second')
  row = db.query(models.GoalDependency).one()
  row_id = row.id
  assert _withdraw(second.id, row_id) == 'withdrawn'
  db.expire_all()
  source = db.query(models.AgentWorkClaim).one()
  assert source.owner_chat_id == first.id and source.completed_at is None and source.released_at is None
  assert claim(second, 'claim-run-second')['follow_status'] == 'withdrawn'
  claim(second, 'claim-run-second', takeover_reason='Owner-authorized transfer', expected_owner_chat_id=first.id)
  claim(first, 'claim-run-first', takeover_reason='Owner-authorized return', expected_owner_chat_id=second.id)
  db.expire_all()
  assert db.get(models.GoalDependency, row_id).result_disposition == 'withdrawn'


@pytest.mark.parametrize('finish_first', [False, True])
def test_claim_resolution_races_real_withdrawal_without_overwriting_saved_facts(db, monkeypatch, finish_first):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Event
  from app import agent_work_claims, decision_cards, goal_dependency_sources
  from app.database import SessionLocal
  from tests.test_agent_work_claims import _fixture
  owner, source, dependent = _fixture(db)
  owner_id, source_id, dependent_id = owner.id, source.id, dependent.id
  for chat_id, run in [(source_id, 'claim-run-first'), (dependent_id, 'claim-run-second')]:
    agent_work_claims.claim_work(db, owner_id=owner_id, chat_id=chat_id,
      run_id=run, work_key='race', summary='Repair host')
  row_id = db.query(models.GoalDependency.id).scalar()
  db.rollback()
  locked, release, competing = Event(), Event(), Event()
  original_fence = decision_cards.fence_decisions
  original_stamp = goal_dependency_sources.stage_stamp_claim_outcome

  def fence(session):
    if finish_first:
      competing.set()
    original_fence(session)
    if not finish_first:
      locked.set()
      assert release.wait(5)

  def stamp(*args, **kwargs):
    result = original_stamp(*args, **kwargs)
    if finish_first:
      locked.set()
      assert release.wait(5)
    return result

  monkeypatch.setattr(decision_cards, 'fence_decisions', fence)
  monkeypatch.setattr(agent_work_claims, 'stage_stamp_claim_outcome', stamp)

  def finish():
    with SessionLocal() as session:
      if not finish_first:
        competing.set()
      return agent_work_claims.finish_work(session, owner_id=owner_id,
        chat_id=source_id, work_key='race', outcome='Verified', release=False)

  with ThreadPoolExecutor(max_workers=2) as pool:
    first = pool.submit(finish if finish_first else lambda: _withdraw(dependent_id, row_id))
    try:
      assert locked.wait(5)
      second = pool.submit((lambda: _withdraw(dependent_id, row_id)) if finish_first else finish)
      assert competing.wait(5)
    finally:
      release.set()
    first.result(5)
    second.result(5)
  db.expire_all()
  row = db.get(models.GoalDependency, row_id)
  assert row.result_disposition == 'withdrawn' and row.received_run_id is None
  assert row.outcome_json['summary'] == 'Repair host'
  assert row.outcome_json.get('kind') == ('completed' if finish_first else None)
  assert db.query(models.AgentWorkClaim).one().completed_at is not None


def test_other_follow_of_same_goal_remains_automatic(db, chat):
  from app.chat_writer import create_chat, StartTurn, QuestionCommit
  from app.decision_cards import stage_wait_for_decision
  _source, card = _setup(db, chat)
  first = _follow(db, chat)
  db.add(create_chat(id='other-source', messages=[]))
  db.commit()
  get_writer().submit(StartTurn(chat_id='other-source', run_token='other-source-run',
    user_msg={'role': 'user', 'content': 'Choose', 'ts': 3})).result(5)
  get_writer().submit(QuestionCommit(chat_id='other-source', run_token='other-source-run',
    snapshot={'id': 'other-source-run', 'role': 'assistant', 'blocks': [dict(card, question_id='other-choice')], 'ts': 4})).result(5)
  db.expire_all()
  second = stage_wait_for_decision(db, chat_id=chat.id, run_id='dependent-run',
    source_chat_id='other-source', question_id='other-choice')
  db.commit()
  assert _withdraw(chat.id, first.id) == 'withdrawn'
  db.expire_all()
  handoff = goal_handoffs(db, db.get(models.ChatGoal, first.waiting_goal_id))
  assert handoff.prevents_settlement
  assert [d.id for d in handoff.dependencies] == [second.id]
  assert handoff.dependencies[0].automatic
  assert second.result_disposition is None
