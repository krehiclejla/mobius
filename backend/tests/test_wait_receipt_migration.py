"""Wait receipt upgrades preserve uncertainty, pending work and idempotence."""
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app import models, schema_migrations as migrations


@pytest.mark.parametrize("source", ["fresh", "previous-release"])
def test_wait_receipt_schema_converges_from_fresh_and_frozen_release(tmp_path, source, monkeypatch):
  monkeypatch.setenv("DATA_DIR", str(tmp_path))
  path = tmp_path / "upgrade.db"
  if source == "previous-release":
    fixture = Path(__file__).parent / "fixtures" / "schema_0013.sql"
    with sqlite3.connect(path) as conn:
      conn.executescript(fixture.read_text())
  eng = create_engine(f"sqlite:///{path}")
  try:
    models.Base.metadata.create_all(eng)
    migrations.run_migrations(eng)
    history = migrations.schema_migration_history(eng)
    migrations.run_migrations(eng)
    assert migrations.schema_migration_history(eng) == history
    assert "0089_wait_result_receipts" in {row["version"] for row in history}
    assert migrations.mapped_schema_gaps(eng) == []
  finally:
    eng.dispose()


@pytest.mark.parametrize("partial", [False, True])
def test_old_latches_are_not_fabricated_provider_receipts_or_rewoken(tmp_path, partial):
  eng = create_engine(f"sqlite:///{tmp_path / 'old-waits.db'}")
  try:
    with eng.begin() as conn:
      conn.execute(text("CREATE TABLE chat_runs (id TEXT PRIMARY KEY)"))
      conn.execute(text("CREATE TABLE chat_waits (id TEXT PRIMARY KEY, status TEXT, "
        "resume_delivered_at TIMESTAMP, cancelled_at TIMESTAMP)"))
      conn.execute(text("INSERT INTO chat_waits VALUES "
        "('old-latch', 'met', '2026-01-01', NULL), "
        "('cancelled', 'cancelled', NULL, '2026-01-02'), "
        "('owed', 'met', NULL, NULL), ('pending', 'armed', NULL, NULL)"))
      if partial:
        conn.execute(text("ALTER TABLE chat_waits ADD COLUMN result_disposition VARCHAR(32)"))
    migrations._add_wait_result_receipts(eng)
    migrations._add_wait_result_receipts(eng)
    with eng.connect() as conn:
      rows = {row.id: row for row in conn.execute(text("SELECT * FROM chat_waits"))}
      assert rows['old-latch'].result_disposition == 'legacy_consumed'
      assert rows['old-latch'].received_run_id is None
      assert rows['old-latch'].resume_delivered_at == rows['old-latch'].result_disposed_at
      assert rows['cancelled'].result_disposition == 'withdrawn'
      assert rows['cancelled'].resume_delivered_at is None
      assert rows['owed'].result_disposition is None
      assert rows['pending'].result_disposition is None
  finally:
    eng.dispose()
