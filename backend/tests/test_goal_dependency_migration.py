"""Legacy work interests remain readable, non-waking history on upgrade."""

import asyncio
import json

import pytest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from app import models, schema_migrations
from app.chat_writer import create_chat


def _seed_legacy_follow(eng, *, suffix, source_status="running", card=False,
                        resolved=False):
  now = datetime(2026, 10, 12, 12)
  with Session(eng) as db:
    db.add(create_chat(id=f"chat-{suffix}", messages=[]))
    source = create_chat(id=f"source-{suffix}", messages=[])
    if card:
      source.pending_question_id = f"question-{suffix}"
    db.add(source)
    db.add(models.ChatGoal(id=f"goal-{suffix}", chat_id=f"chat-{suffix}",
      objective="Follow work", status="open", created_at=now - timedelta(days=2)))
    db.add(models.ChatGoal(id=f"source-goal-{suffix}", chat_id=f"source-{suffix}",
      objective="Own work", status="open", created_at=now - timedelta(days=2)))
    db.add(models.ChatRun(id=f"run-{suffix}", chat_id=f"source-{suffix}",
      goal_id=f"source-goal-{suffix}", status=source_status))
    db.add(models.AgentWorkClaim(id=f"claim-{suffix}", owner_id=1,
      work_key=f"work-{suffix}", summary="Work", owner_chat_id=f"source-{suffix}",
      owner_run_id=f"run-{suffix}", owner_goal_id=f"source-goal-{suffix}",
      claimed_at=now - timedelta(days=2)))
    db.add(models.AgentWorkInterest(id=f"interest-{suffix}",
      claim_id=f"claim-{suffix}", chat_id=f"chat-{suffix}",
      goal_id=f"goal-{suffix}", created_at=now - timedelta(days=1),
      resolved_at=now if resolved else None))
    db.commit()


def test_fresh_upgrade_and_repeat_preserve_legacy_rows_without_dependencies(tmp_path):
  eng = create_engine(f"sqlite:///{tmp_path / 'follows.db'}")
  models.Base.metadata.create_all(eng)
  for suffix, status, card, resolved in (
    ("running", "running", False, False),
    ("card", "completed", True, False),
    ("completed", "completed", False, False),
    ("failed", "failed", False, False),
    ("resolved", "completed", False, True),
    ("dismissed", "running", False, False),
  ):
    _seed_legacy_follow(eng, suffix=suffix, source_status=status,
                        card=card, resolved=resolved)
  with eng.begin() as conn:
    conn.execute(text("UPDATE agent_work_claims SET acquisition_id=NULL"))
    conn.execute(text("UPDATE chat_goals SET status='dismissed' WHERE id='goal-dismissed'"))
    legacy = conn.execute(text("SELECT * FROM agent_work_interests ORDER BY id")).all()
    conn.execute(text("DROP TABLE goal_dependencies"))
    conn.execute(text("DROP INDEX ix_agent_work_claims_acquisition_id"))
    conn.execute(text("ALTER TABLE agent_work_claims DROP COLUMN acquisition_id"))
    conn.execute(text("ALTER TABLE chat_runs DROP COLUMN admitted_dependency_results_json"))
  schema_migrations._add_goal_dependencies(eng)
  with eng.connect() as conn:
    ids = dict(conn.execute(text(
      "SELECT id, acquisition_id FROM agent_work_claims"
    )).all())
    assert conn.execute(text("SELECT * FROM agent_work_interests ORDER BY id")).all() == legacy
    assert all(ids.values())
    assert conn.execute(text("SELECT count(*) FROM goal_dependencies")).scalar_one() == 0
  schema_migrations._add_goal_dependencies(eng)
  with eng.connect() as conn:
    assert dict(conn.execute(text(
      "SELECT id, acquisition_id FROM agent_work_claims"
    )).all()) == ids
    assert conn.execute(text(
      "SELECT * FROM agent_work_interests ORDER BY id"
    )).all() == legacy
    assert conn.execute(text("SELECT count(*) FROM goal_dependencies")).scalar_one() == 0
  assert "owner_suppressed_at" in {
    col["name"] for col in inspect(eng).get_columns("goal_dependencies")
  }


def test_pending_dependency_indexes_are_append_only_and_idempotent(tmp_path):
  eng = create_engine(f"sqlite:///{tmp_path / 'pending-indexes.db'}")
  models.Base.metadata.create_all(eng)
  schema_migrations._index_pending_goal_dependencies(eng)
  schema_migrations._index_pending_goal_dependencies(eng)
  indexes = {item["name"] for item in inspect(eng).get_indexes("goal_dependencies")}
  assert {"ix_goal_dependencies_pending_claims",
    "ix_goal_dependencies_pending_delivery"} <= indexes


@pytest.mark.parametrize("missing", ["agent_work_claims", "agent_coordination_messages", "chats"])
def test_legacy_claim_notice_cutover_accepts_partial_historical_schema(tmp_path, missing):
  eng = create_engine(f"sqlite:///{tmp_path / 'partial-notices.db'}")
  models.Base.metadata.create_all(eng)
  with eng.begin() as conn:
    conn.execute(text(f"DROP TABLE {missing}"))
  schema_migrations._retire_legacy_claim_notice_carriers(eng)
  eng.dispose()


def test_legacy_claim_notice_cutover_is_ledgered_and_preserves_transfers(tmp_path,
                                                                        monkeypatch):
  eng = create_engine(f"sqlite:///{tmp_path / 'claim-notices.db'}")
  models.Base.metadata.create_all(eng)
  now = datetime(2026, 10, 12, 12)
  with Session(eng) as db:
    db.add(models.Owner(id=1, username="owner", hashed_password="unused"))
    db.add_all([create_chat(id=chat_id, messages=[]) for chat_id in
      ("source", "waiting")])
    db.flush()
    db.add_all([
      models.AgentWorkClaim(id="finished", owner_id=1, work_key="finished",
        summary="Finished", owner_chat_id="source", owner_run_id="run",
        revision=2, completed_at=now),
      models.AgentWorkClaim(id="transferred", owner_id=1, work_key="transferred",
        summary="Transferred", owner_chat_id="source", owner_run_id="run",
        revision=2, previous_owner_chat_id="waiting"),
    ])
    db.flush()
    for identity, claim_id, revision in (
      ("terminal", "finished", 2), ("old-revision", "finished", 1),
      ("transfer", "transferred", 2),
    ):
      db.add(models.AgentCoordinationMessage(id=identity, room_kind="workspace",
        room_id="owner:1", from_chat_id="source", to_chat_id="waiting",
        from_run_id=f"work-claim:{claim_id}", send_id=f"revision:{revision}",
        kind="claim_notice", delivery="interrupt", body=identity))
    def carrier(cid, ids):
      return {"role": "user", "kind": "peer_message", "hidden": True,
        "cid": cid, "peer_message_through": {"id": ids[-1]},
        "content": "<agent_coordination>" + json.dumps({
          "self_chat_id": "waiting", "messages": [{"id": item} for item in ids],
        }) + "</agent_coordination>"}
    waiting = db.get(models.Chat, "waiting")
    waiting.pending_messages = [carrier("terminal-only", ["terminal"]),
      carrier("transfer-only", ["transfer"]),
      carrier("mixed", ["terminal", "transfer"]),
      {"role": "user", "cid": "owner", "content": "Keep me"}]
    db.add(models.ChatRun(id="receipt-run", chat_id="waiting",
      root_run_id="receipt-run", status="completed", provider="codex",
      peer_message_through_created_at=now, peer_message_through_id="receipt"))
    db.commit()

  version = "0094_retire_legacy_claim_notice_carriers"
  monkeypatch.setattr(schema_migrations, "_SCHEMA_MIGRATIONS", (
    (version, schema_migrations._retire_legacy_claim_notice_carriers),
  ))
  schema_migrations.run_migrations(eng)
  schema_migrations.run_migrations(eng)
  with Session(eng) as db:
    notices = {row.id: row.delivery for row in
      db.query(models.AgentCoordinationMessage).all()}
    assert notices == {"terminal": "next_turn", "old-revision": "interrupt",
      "transfer": "interrupt"}
    waiting = db.get(models.Chat, "waiting")
    assert [item["cid"] for item in waiting.pending_messages] == [
      "transfer-only", "mixed", "owner"]
    receipt = db.get(models.ChatRun, "receipt-run")
    assert receipt.peer_message_through_created_at == now
    assert receipt.peer_message_through_id == "receipt"
    assert schema_migrations._applied_migrations(eng) == {version}
  eng.dispose()


@pytest.mark.parametrize("missing", ["table", "suppression_field"])
def test_partial_dependency_migration_reuses_acquisition_and_repairs_field(tmp_path, missing):
  eng = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
  models.Base.metadata.create_all(eng)
  _seed_legacy_follow(eng, suffix="partial")
  with eng.begin() as conn:
    conn.execute(text(
      "UPDATE agent_work_claims SET acquisition_id='first-generation'"
    ))
    conn.execute(text("DROP TABLE goal_dependencies" if missing == "table" else
                      "ALTER TABLE goal_dependencies DROP COLUMN owner_suppressed_at"))
  schema_migrations._add_goal_dependencies(eng)
  schema_migrations._add_goal_dependencies(eng)
  with eng.connect() as conn:
    assert conn.execute(text(
      "SELECT acquisition_id FROM agent_work_claims"
    )).scalar_one() == "first-generation"
    assert conn.execute(text("SELECT count(*) FROM goal_dependencies")).scalar_one() == 0
    assert conn.execute(text("SELECT count(*) FROM agent_work_interests")).scalar_one() == 1
  assert "owner_suppressed_at" in {
    col["name"] for col in inspect(eng).get_columns("goal_dependencies")
  }


def test_upgrade_startup_reconcile_delivery_never_wakes_legacy_but_new_follow_works(db):
  from app import chat, goal_dependencies, goal_dependency_sources
  from app.chat_writer import ReconcileClaimSources, ReconcileStartupChat, get_writer

  eng = db.get_bind()
  for suffix, status, card in (
    ("running", "running", False),
    ("card", "completed", True),
    ("completed", "completed", False),
    ("failed", "failed", False),
  ):
    _seed_legacy_follow(eng, suffix=suffix, source_status=status, card=card)
  schema_migrations._add_goal_dependencies(eng)
  writer = get_writer()
  assert writer.submit(ReconcileStartupChat(
    chat_id="source-running", messages=[], running_run_ids=("run-running",),
    recovered_at=datetime.now(UTC),
  )).result(timeout=5)
  assert writer.submit(ReconcileClaimSources()).result(timeout=5) == 0
  assert asyncio.run(goal_dependencies.deliver_pending_dependencies()) == 0
  with Session(eng) as session:
    assert session.get(models.ChatRun, "run-running").status == "interrupted"
    assert session.query(models.AgentWorkInterest).count() == 4
    assert session.query(models.GoalDependency).count() == 0
    assert chat._nonempty_pending_queues(session) == []
    # A new, exact-acquisition follow is still registered on this upgraded DB.
    session.add(models.ChatRun(id="waiting-new", chat_id="chat-completed",
      goal_id="goal-completed", status="running"))
    session.flush()
    claim = session.get(models.AgentWorkClaim, "claim-completed")
    assert goal_dependency_sources.stage_follow_claim(
      session, claim, chat_id="chat-completed", run_id="waiting-new"
    ) == "followed"
    assert session.query(models.GoalDependency).count() == 1
    assert session.query(models.AgentWorkInterest).count() == 4
    session.commit()


@pytest.mark.parametrize("foreign_keys", [False, True])
def test_retired_audit_rows_have_no_guarantee_beyond_chat_purge(tmp_path, foreign_keys):
  """Keep the existing FK policy; retained rows never become runtime follows."""
  eng = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
  models.Base.metadata.create_all(eng)
  with Session(eng) as db:
    db.add(models.Owner(id=1, username="owner", hashed_password="unused"))
    db.add(create_chat(id="audit-chat", title="Old", messages=[]))
    db.add(models.AgentWorkClaim(id="audit-claim", owner_id=1,
      work_key="audit:work", summary="Work", owner_run_id="old-run"))
    db.commit()
    db.add(models.AgentWorkInterest(id="audit-row", claim_id="audit-claim",
      chat_id="audit-chat", goal_id="old-goal"))
    db.commit()
  with eng.connect() as conn:
    conn.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
    assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == int(foreign_keys)
    conn.execute(text("DELETE FROM chats WHERE id='audit-chat'"))
    assert conn.execute(text("SELECT count(*) FROM agent_work_interests")).scalar_one() == (0 if foreign_keys else 1)
    assert conn.execute(text("SELECT count(*) FROM goal_dependencies")).scalar_one() == 0
    conn.commit()
  eng.dispose()


def _seed_transfer_notice_upgrade(eng):
  with eng.begin() as conn:
    conn.execute(text("CREATE TABLE apps (id TEXT)"))
    conn.execute(text(
      "CREATE TABLE agent_work_claims (id TEXT, revision INTEGER, "
      "notification_revision INTEGER, previous_owner_chat_id TEXT, "
      "completed_at TEXT, released_at TEXT)"
    ))
    conn.execute(text(
      "INSERT INTO agent_work_claims VALUES "
      "('pending', 2, 1, 'old', NULL, NULL), "
      "('acknowledged', 2, 2, 'old', NULL, NULL), "
      "('completed', 3, 1, 'old', 'now', NULL), "
      "('released', 3, 1, 'old', NULL, 'now'), "
      "('never-transferred', 1, 0, NULL, NULL, NULL)"
    ))


def _transfer_notice_revisions(eng):
  with eng.connect() as conn:
    return dict(conn.execute(text(
      "SELECT id, transfer_notice_revision FROM agent_work_claims"
    )).all())


def test_transfer_notice_migration_backfills_only_known_open_transfer_events(tmp_path):
  eng = create_engine(f"sqlite:///{tmp_path / 'transfer.db'}")
  _seed_transfer_notice_upgrade(eng)
  schema_migrations._add_transfer_notice_revision(eng)
  schema_migrations._add_transfer_notice_revision(eng)
  assert _transfer_notice_revisions(eng) == {
    'pending': 2, 'acknowledged': None, 'completed': None,
    'released': None, 'never-transferred': None,
  }
  # Later claim updates must not change the transfer event's captured identity.
  with eng.begin() as conn:
    conn.execute(text("UPDATE agent_work_claims SET revision=3 WHERE id='pending'"))
  schema_migrations._add_transfer_notice_revision(eng)
  assert _transfer_notice_revisions(eng)['pending'] == 2
  eng.dispose()


@pytest.mark.parametrize("interrupted", [False, True], ids=["column-present", "interrupted-ddl"])
def test_transfer_notice_upgrade_resumes_before_recording_completion(tmp_path, monkeypatch, interrupted):
  eng = create_engine(f"sqlite:///{tmp_path / 'partial-transfer.db'}")
  _seed_transfer_notice_upgrade(eng)
  version = "0092_transfer_notice_revision"
  monkeypatch.setattr(schema_migrations, "_SCHEMA_MIGRATIONS", (
    (version, schema_migrations._add_transfer_notice_revision),
  ))
  schema_migrations._ensure_migration_ledger(eng)
  if interrupted:
    def fail_backfill(conn, cursor, statement, parameters, context, executemany):
      if statement.startswith("UPDATE agent_work_claims SET transfer_notice_revision"):
        raise RuntimeError("interrupted after add-column")

    event.listen(eng, "before_cursor_execute", fail_backfill)
    try:
      with pytest.raises(RuntimeError, match="interrupted after add-column"):
        schema_migrations.run_migrations(eng)
    finally:
      event.remove(eng, "before_cursor_execute", fail_backfill)
  else:
    with eng.begin() as conn:
      conn.execute(text("ALTER TABLE agent_work_claims ADD COLUMN transfer_notice_revision INTEGER"))

  assert "transfer_notice_revision" in {
    column["name"] for column in inspect(eng).get_columns("agent_work_claims")
  }
  assert version not in schema_migrations._applied_migrations(eng)
  assert _transfer_notice_revisions(eng)['pending'] is None
  schema_migrations.run_migrations(eng)
  assert _transfer_notice_revisions(eng) == {
    'pending': 2, 'acknowledged': None, 'completed': None,
    'released': None, 'never-transferred': None,
  }
  assert schema_migrations._applied_migrations(eng) == {version}
  schema_migrations.run_migrations(eng)
  assert _transfer_notice_revisions(eng)['pending'] == 2
  assert schema_migrations._applied_migrations(eng) == {version}
  eng.dispose()
