"""Dependency context is exact, quiet and truthful across owner-facing surfaces."""
import pytest
from app import models
from app.chat_writer import create_chat
from app.goal_dependencies import (source_dependency_backlinks,
  terminal_dependency_summaries_by_message_index, goal_dependency_history,
  goal_dependency_handoffs)
from app.timeutil import now_naive_utc
from tests.test_decision_dependencies import _setup, _follow
from tests.test_goal_dependency_delivery import _run, _dependency


def test_detail_without_dependencies_skips_receipt_index_and_keeps_blockless_rows(
    db, chat, client, auth, monkeypatch):
  from app import transcript_rows
  transcript_rows.append(db, chat, {'role': 'user', 'id': 'plain', 'content': 'Hello'})
  db.commit()
  class UnreadableMessages:
    def __iter__(self):
      raise AssertionError('No dependency receipts should not index the transcript')
  assert terminal_dependency_summaries_by_message_index(db, chat.id, UnreadableMessages()) == {}
  response = client.get(f'/api/chats/{chat.id}', headers=auth)
  assert response.status_code == 200, response.text
  assert 'blocks' not in response.json()['messages'][0]


def test_exact_anchor_window_is_bounded_without_losing_requested_row():
  from app.routes.chats import _chat_detail_window
  messages = [{'role': 'user', 'id': f'row-{i}'} for i in range(500)]
  messages[10] = {'role': 'assistant', 'id': 'row-10', 'blocks': [
    {'type': 'question', 'question_id': 'historical'}]}
  page, start, found = _chat_detail_window(messages, limit=20, before=None,
    anchor_key='question:historical')
  assert found is True and start == 9
  assert page[1]['id'] == 'row-10'
  assert len(page) == 21


def test_handoff_blocker_set_excludes_unproduced_names(db, chat, monkeypatch):
  from app import goal_dependency_sources
  run = _run(db, chat, 'declare', 'goal')
  _dependency(db, chat, run, settled=False)
  goal = db.get(models.ChatGoal, 'goal')
  for obsolete in ('source_deleted', 'source_goal_missing',
      'acquisition_changed', 'source_settled_without_outcome',
      'owner_goal_not_open'):
    monkeypatch.setattr(goal_dependency_sources, 'dependency_source_blocker',
      lambda *args, **kwargs: obsolete)
    assert goal_dependency_handoffs(db, goal)[0]['automatic']


def test_card_backlinks_are_exact_deduplicated_and_read_only(db, chat, client, auth):
  source, _ = _setup(db, chat)
  chat.title = 'Finish blocked update'
  row = _follow(db, chat)
  other = create_chat(id='other', title='Another dependent')
  db.add(other)
  db.add(models.ChatGoal(id='other-goal', chat_id='other', objective='Other work'))
  db.add(models.ChatGoal(id='second-goal', chat_id=chat.id, objective='Second work'))
  db.flush()
  for id, waiting_chat, goal in [('second', chat.id, 'second-goal'), ('other-link', 'other', 'other-goal')]:
    db.add(models.GoalDependency(id=id, waiting_chat_id=waiting_chat, waiting_goal_id=goal,
      created_by_run_id='dependent-run', target_kind='owner_card', target_key='card:source:choice',
      target_chat_id='source', target_question_id='choice'))
  db.commit()
  expected = [{'chat_id': 'other', 'title': 'Another dependent'}, {'chat_id': chat.id, 'title': 'Finish blocked update'}]
  assert source_dependency_backlinks(db, source.id, question_ids=['choice', 'unrelated']) == {'choice': expected}
  detail = client.get('/api/chats/source', headers=auth).json()
  assert 'blocks' not in detail['messages'][0]
  cards = [b for m in detail['messages'] for b in m.get('blocks', []) if b.get('type') == 'question']
  assert cards[0]['needed_by'] == expected
  assert source_dependency_backlinks(db, chat.id, question_ids=['choice']) == {}
  assert row.resolved_at is None and row.received_run_id is None
  assert db.get(models.Chat, source.id).pending_question_id == 'choice'
  db.get(models.ChatGoal, 'other-goal').status = 'completed'
  db.get(models.ChatGoal, 'dependent-goal').status = 'stopped'
  db.commit()
  assert source_dependency_backlinks(db, source.id, question_ids=['choice']) == {'choice': expected[1:]}
  chat.deleted_at = now_naive_utc()
  db.commit()
  assert source_dependency_backlinks(db, source.id, question_ids=['choice']) == {}


def test_claim_backlink_moves_with_exact_acquisition_owner_not_another_goal(db, chat, auth):
  owner = db.query(models.Owner).one()
  run = _run(db, chat, 'follower', 'dependent')
  row = _dependency(db, chat, run, settled=False)
  source = create_chat(id='source', title='Repair')
  next_source = create_chat(id='next-source', title='New executor')
  db.add_all([source, next_source])
  db.flush()
  claim = models.AgentWorkClaim(id='claim', acquisition_id='acquisition', owner_id=owner.id,
    work_key='repair', summary='Repair host', owner_chat_id='source', owner_goal_id='source-goal', owner_run_id='source-run')
  db.add(claim)
  db.commit()
  assert source_dependency_backlinks(db, 'source', goal_id='source-goal')['source-goal'][0]['chat_id'] == chat.id
  assert source_dependency_backlinks(db, 'source', goal_id='unrelated') == {}
  claim.owner_chat_id, claim.owner_goal_id = 'next-source', 'next-goal'
  db.commit()
  assert source_dependency_backlinks(db, 'source', goal_id='source-goal') == {}
  assert source_dependency_backlinks(db, 'next-source', goal_id='next-goal')['next-goal']
  claim.acquisition_id = 'new-acquisition'
  db.commit()
  assert source_dependency_backlinks(db, 'next-source', goal_id='next-goal') == {}
  assert row.target_acquisition_id == 'acquisition'


@pytest.mark.parametrize('state', ['automatic', 'held', 'failed', 'local-input'])
def test_drawer_runtime_and_detail_agree_on_dependency_continuation(client, auth, db, chat, state):
  from app.goals import _new_hold
  from tests.test_chat_deferred_handoff import read
  _setup(db, chat)
  _follow(db, chat)
  run = db.get(models.ChatRun, 'dependent-run')
  run.goal_objective = 'Finish update'
  goal = db.get(models.ChatGoal, 'dependent-goal')
  if state == 'held':
    goal.status = 'stopped'
    goal.hold_json = _new_hold(cause='deferred', actor='agent', source_id=run.id, run_id=run.id, reason='Later')
  if state == 'failed':
    row = db.query(models.GoalDependency).one()
    row.target_kind, row.target_claim_id = 'claim_acquisition', 'missing'
  if state == 'local-input':
    chat.pending_question_id = 'own-card'
  db.commit()
  listing, runtime = read(client, auth, chat)
  expected = {'automatic': 'automatic', 'held': 'on_hold', 'failed': 'recovery', 'local-input': 'owner_input'}[state]
  assert listing['handoff']['kind'] == expected
  assert listing['waiting'] == (expected == 'automatic')
  if state in {'held', 'failed'}:
    assert not runtime['goal']['dependencies'][0]['automatic']


@pytest.mark.parametrize('disposition', [None, 'delivered', 'withdrawn', 'retired_by_goal_outcome'])
def test_typed_history_belongs_to_exact_receipt_or_declaration_never_newest_goal(db, chat, disposition):
  run = _run(db, chat, 'declare', 'goal')
  row = _dependency(db, chat, run)
  row.result_disposition = disposition
  row.received_run_id = 'receive' if disposition == 'delivered' else None
  db.commit()
  messages = [{'role': 'assistant', 'id': id} for id in ['declare', 'unrelated', 'receive']]
  history = terminal_dependency_summaries_by_message_index(db, chat.id, messages)
  index = 2 if disposition == 'delivered' else 0
  assert list(history) == [index]
  assert history[index][0]['outcome']['kind'] == 'released'
  assert history[index][0]['condition'] == 'Host repair'
  assert history[index][0]['delivery_pending'] == (disposition is None)
  assert terminal_dependency_summaries_by_message_index(db, chat.id, [messages[1]]) == {}
  assert bool(goal_dependency_history(db, db.get(models.ChatGoal, 'goal'))) == (disposition is not None)


def test_dependency_trace_skips_hidden_segments_of_both_exact_runs(db, chat):
  run = _run(db, chat, 'declare', 'goal')
  row = _dependency(db, chat, run)
  row.result_disposition, row.received_run_id = 'delivered', 'receive'
  db.commit()
  messages = [{'role': 'assistant', 'id': 'receive', 'hidden': True},
    {'role': 'assistant', 'id': 'declare', 'hidden': True},
    {'role': 'assistant', 'id': 'unrelated'},
    {'role': 'assistant', 'id': 'declare:assistant:1'},
    {'role': 'assistant', 'id': 'receive:assistant:1'}]
  assert list(terminal_dependency_summaries_by_message_index(db, chat.id, messages)) == [4]
  assert list(terminal_dependency_summaries_by_message_index(db, chat.id, messages[:-1])) == [3]


def test_retired_pending_claim_keeps_source_context_after_reacquisition(db, chat, auth):
  from app.goal_dependencies import stage_retire_goal_dependencies
  run = _run(db, chat, 'declare', 'goal')
  row = _dependency(db, chat, run, settled=False)
  source = create_chat(id='source', title='Original repair')
  db.add(source)
  db.flush()
  claim = models.AgentWorkClaim(id='claim', acquisition_id='acquisition', owner_id=db.query(models.Owner).one().id,
    work_key='repair', summary='Repair host before release', owner_chat_id='source', owner_run_id='source-run')
  db.add(claim)
  db.commit()
  assert stage_retire_goal_dependencies(db, chat.id, 'goal') == 1
  claim.acquisition_id, claim.summary = 'new-acquisition', 'Unrelated later operation'
  db.commit(); db.expire_all()
  history = goal_dependency_history(db, db.get(models.ChatGoal, 'goal'))[0]
  assert history['condition'] == 'Repair host before release'
  assert history['outcome'].get('kind') is None
  assert row.resolved_at is None and row.received_run_id is None
  assert history['disposition'] == 'retired_by_goal_outcome'


@pytest.mark.parametrize('suffix', ['', '/runtime'])
@pytest.mark.parametrize('state', ['pending', 'owed', 'delivered', 'terminal'])
@pytest.mark.parametrize('kind', ['claim_acquisition', 'owner_card'])
@pytest.mark.parametrize('saved_title', [None, 'Title saved with the result'])
def test_embed_dependency_facts_do_not_enrich_or_link_foreign_chats(client, owner_token, db, suffix, state, kind, saved_title):
  from app import transcript_rows
  from tests.test_chat_embed_capability import _session, _embed_headers
  _, _, chat_id, _, session = _session(client, owner_token, name='dependency-embed')
  chat = db.get(models.Chat, chat_id)
  source = create_chat(id='private-chat', title='Private renamed title', messages=[])
  db.add(source); db.commit()
  run = _run(db, chat, 'embed-declare', 'embed-goal')
  run.goal_objective = 'Own work'
  row = _dependency(db, chat, run, settled=state != 'pending')
  row.target_kind, row.target_chat_id = kind, source.id
  row.target_question_id = 'private-card'
  db.add(models.AgentWorkClaim(id='claim', acquisition_id='acquisition',
    owner_id=db.query(models.Owner).one().id, work_key='private-key',
    summary='Private live condition', owner_chat_id=source.id, owner_run_id='private-run'))
  db.add(models.SavedDecisionCard(chat_id=source.id, question_id='private-card',
    family='question', summary='Private live condition'))
  if state != 'pending':
    row.outcome_json = {'kind': 'released' if kind == 'claim_acquisition' else 'answered',
      'summary': 'Saved condition', 'outcome': 'Saved result', 'source_chat_id': source.id,
      'source_question_id': 'private-card', 'source_run_id': 'private-run',
      **({'source_chat_title': saved_title} if saved_title else {})}
  if state in {'delivered', 'terminal'}:
    row.result_disposition, row.received_run_id = 'delivered', run.id
  if state == 'terminal':
    goal = db.get(models.ChatGoal, 'embed-goal')
    goal.status, goal.result, goal.completion_run_id = 'completed', 'Own answer', run.id
    goal.completed_at = run.ended_at = now_naive_utc()
  transcript_rows.append(db, chat, {'role': 'assistant', 'id': run.id,
    'ts': now_naive_utc().timestamp() * 1000, 'blocks': [{'type': 'text', 'text': 'Own answer'}]})
  db.commit()
  headers = _embed_headers(session)
  assert client.get('/api/chats/private-chat', headers=headers).status_code in (403, 404)
  response = client.get(f'/api/chats/{chat_id}{suffix}', headers=headers)
  assert response.status_code == 200, response.text
  for private in ['Private renamed title', 'Private live condition', 'private-chat', 'private-card', 'private-run', 'private-key']:
    assert private not in response.text
  goal = response.json()['goal']
  projection = goal['dependency_history' if state in {'delivered', 'terminal'} else 'dependencies'][0]
  assert projection['condition'] == ('Shared work' if state == 'pending' else 'Saved condition')
  assert projection['source_chat_title'] == (saved_title if state != 'pending' and saved_title else 'Another chat')
  assert projection['source_chat_id'] is None and projection['source_question_id'] is None
  assert 'needed_by' not in goal
  if state != 'pending':
    assert projection['outcome'] == {'kind': 'released' if kind == 'claim_acquisition' else 'answered',
      'summary': 'Saved condition', 'outcome': 'Saved result'}
  if not suffix and state != 'pending':
    receipts = [s for m in response.json()['messages'] for s in m.get('wait_summaries', [])]
    assert receipts and receipts[0]['source_chat_id'] is None
    if state == 'terminal':
      goals = [s for m in response.json()['messages'] for s in m.get('goal_summaries', [])]
      assert goals and goals[0]['dependency_history'][0]['source_chat_id'] is None


def test_restricted_history_never_queries_foreign_metadata(db, chat, monkeypatch):
  from app.goal_dependencies import dependency_summary
  run = _run(db, chat, 'declare-restricted', 'own-goal')
  row = _dependency(db, chat, run)
  def forbidden(*args, **kwargs):
    raise AssertionError('Restricted history must use only its already-saved facts')
  monkeypatch.setattr(db, 'get', forbidden)
  assert dependency_summary(db, row, include_foreign_context=False)['condition'] == 'Host repair'


def test_question_anchor_search_does_not_hydrate_unrelated_history(db, chat, client, auth, monkeypatch):
  from app import transcript_rows
  from app.chat_transcript import MaterializedHistory
  for index in range(500):
    body = {'role': 'assistant', 'id': f'row-{index}', 'blocks': [{'type': 'text', 'text': 'Unrelated history'}]}
    if index == 10:
      body['blocks'] = [{'type': 'question', 'question_id': 'historical',
        'questions': [{'question': 'A prior decision'}]}]
    transcript_rows.append(db, chat, body)
  db.commit()
  def unexpected(self):
    raise AssertionError('Question reveal must not hydrate the full transcript')
  monkeypatch.setattr(MaterializedHistory, '__iter__', unexpected)
  response = client.get(f'/api/chats/{chat.id}?anchor=question:historical&limit=20', headers=auth)
  assert response.status_code == 200, response.text
  data = response.json()
  assert data['requested_anchor_found'] and data['offset'] == 9
  assert data['messages'][1]['id'] == 'row-10'
  assert len(data['messages']) == 21 and data['total'] == 500
  missing = client.get(f'/api/chats/{chat.id}?anchor=question:missing&limit=20', headers=auth)
  assert missing.status_code == 200, missing.text
  assert missing.json()['requested_anchor_found'] is False
  assert len(missing.json()['messages']) == 20


def test_dependency_receipt_paging_hydrates_only_exact_on_page_anchors(db, chat, monkeypatch):
  from app import goal_dependencies
  run = _run(db, chat, 'declare', 'goal')
  row = _dependency(db, chat, run)
  row.result_disposition, row.received_run_id = 'delivered', 'receive'
  other_run = _run(db, chat, 'off-page', 'other-goal')
  other = _dependency(db, chat, other_run, 'other')
  other.created_by_run_id, other.received_run_id = 'off-page', None
  db.commit()
  messages = [{'role': 'assistant', 'id': identity} for identity in
    ['declare', 'off-page', 'receive', 'receive:assistant:1']]
  summaries = []
  original = goal_dependencies.dependency_summary
  def summarize(db, dependency, **kwargs):
    summaries.append(dependency.id)
    return original(db, dependency, **kwargs)
  monkeypatch.setattr(goal_dependencies, 'dependency_summary', summarize)
  # A receipt present outside the window must not fall back to its declaration.
  assert terminal_dependency_summaries_by_message_index(db, chat.id, messages,
    message_start=0, message_end=1) == {}
  assert summaries == []
  history = terminal_dependency_summaries_by_message_index(db, chat.id, messages,
    message_start=2, message_end=4)
  assert list(history) == [2] and summaries == [row.id]
  summaries.clear()
  assert terminal_dependency_summaries_by_message_index(db, chat.id, messages,
    message_start=3, message_end=4) == {}
  assert summaries == []


def test_chat_detail_passes_its_page_window_to_dependency_receipts(db, chat, client, auth, monkeypatch):
  from app import goal_dependencies, transcript_rows
  for index in range(5):
    transcript_rows.append(db, chat, {'role': 'user', 'id': f'row-{index}', 'content': 'Hello'})
  db.commit()
  windows = []
  def summaries(_db, chat_id, messages, *, message_start, message_end, **kwargs):
    assert chat_id == chat.id and len(messages) == 5
    windows.append((message_start, message_end))
    return {}
  monkeypatch.setattr(goal_dependencies, 'terminal_dependency_summaries_by_message_index', summaries)
  response = client.get(f'/api/chats/{chat.id}?limit=2&before=3', headers=auth)
  assert response.status_code == 200, response.text
  assert windows == [(1, 3)]
