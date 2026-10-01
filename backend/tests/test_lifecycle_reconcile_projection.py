"""Settled history must not be materialized just to prove no repair is needed."""

from datetime import datetime

from sqlalchemy import event

from app import models
from app.agent_lifecycle import reconcile_run_updates


def test_clean_lifecycle_history_does_not_hydrate_run_objects(db, chat):
  db.add(models.ChatRun(
    id="settled-projection", chat_id=chat.id, provider="codex",
    status="completed", started_at=datetime(2026, 9, 1),
    ended_at=datetime(2026, 9, 1, 0, 1),
    continuation_json={"context": "x" * 100_000},
  ))
  db.commit()
  db.expunge_all()
  hydrated = []

  def record_loaded(_session, instance):
    if isinstance(instance, (models.ChatRun, models.ChatRunUpdate)):
      hydrated.append(type(instance).__name__)

  event.listen(db, "loaded_as_persistent", record_loaded)
  try:
    assert reconcile_run_updates(db) == 0
  finally:
    event.remove(db, "loaded_as_persistent", record_loaded)
  assert hydrated == []
