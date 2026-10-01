"""Core helper ownership migrates without discarding app-owned history."""
from sqlalchemy import create_engine, event, inspect, text
from app.schema_migrations import _allow_chat_owned_delegations
from app import models


def test_existing_delegations_keep_history_indexes_triggers_and_incoming_keys(tmp_path):
  eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
  @event.listens_for(eng, "connect")
  def foreign_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")
  with eng.begin() as c:
    for sql in [
      "CREATE TABLE apps(id INTEGER PRIMARY KEY)",
      "INSERT INTO apps VALUES(7)",
      "CREATE TABLE delegations(id TEXT PRIMARY KEY, app_id INTEGER NOT NULL REFERENCES apps(id), legacy_payload TEXT NOT NULL, UNIQUE(id,app_id))",
      "CREATE INDEX ix_old_delegations_app ON delegations(app_id)",
      "CREATE TABLE observations(id TEXT PRIMARY KEY, delegation_id TEXT REFERENCES delegations(id))",
      "CREATE TABLE audit(id TEXT)",
      "CREATE TRIGGER old_audit AFTER INSERT ON delegations BEGIN INSERT INTO audit VALUES(new.id); END",
      "INSERT INTO delegations VALUES('old',7,'retain everything')",
      "INSERT INTO observations VALUES('seen','old')",
    ]:c.execute(text(sql))
  _allow_chat_owned_delegations(eng)
  _allow_chat_owned_delegations(eng)
  assert next(c for c in inspect(eng).get_columns("delegations") if c["name"]=="app_id")["nullable"]
  with eng.begin() as c:
    assert c.execute(text("SELECT * FROM delegations")).one()==("old",7,"retain everything")
    c.execute(text("INSERT INTO delegations VALUES('core',NULL,'new')"))
    assert c.execute(text("SELECT id FROM audit ORDER BY id")).scalars().all()==["core","old"]
    assert c.execute(text("PRAGMA foreign_key_check")).all()==[]
    assert c.execute(text("PRAGMA foreign_keys")).scalar()==1
    assert c.execute(text("SELECT name FROM sqlite_master WHERE name='ix_old_delegations_app'")).scalar()
    assert c.execute(text("SELECT delegation_id FROM observations")).scalar()=="old"


def test_fresh_core_delegation_schema_needs_no_table_rebuild(tmp_path):
  eng=create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
  models.Base.metadata.create_all(eng)
  with eng.connect() as c:
    before=c.execute(text("SELECT sql FROM sqlite_master WHERE name='delegations'")).scalar()
  _allow_chat_owned_delegations(eng)
  with eng.connect() as c:
    assert c.execute(text("SELECT sql FROM sqlite_master WHERE name='delegations'")).scalar()==before


def test_failed_table_replacement_rolls_back_old_rows_and_foreign_key_mode(tmp_path, monkeypatch):
  import pytest
  eng = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
  @event.listens_for(eng, "connect")
  def enable_keys(connection, _record):
    connection.execute("PRAGMA foreign_keys=ON")
  with eng.begin() as conn:
    conn.execute(text("CREATE TABLE delegations(id TEXT PRIMARY KEY, app_id INTEGER NOT NULL)"))
    conn.execute(text("INSERT INTO delegations VALUES('old',7)"))
    before = conn.execute(text("PRAGMA foreign_keys")).scalar()
  real_connection = eng.raw_connection

  class FailingCursor:
    def __init__(self, cursor):
      self.cursor = cursor
    def execute(self, sql):
      if sql.startswith("ALTER TABLE delegations__core_0074"):
        raise RuntimeError("interrupted replacement")
      return self.cursor.execute(sql)
    def close(self):
      self.cursor.close()

  class FailingConnection:
    def __init__(self):
      self.raw = real_connection()
    def cursor(self):
      return FailingCursor(self.raw.cursor())
    def commit(self):
      self.raw.commit()
    def rollback(self):
      self.raw.rollback()
    def close(self):
      self.raw.close()

  # Inspector uses engine connections; only the migration's raw transaction
  # gets the fault after DROP, proving rollback covers the dangerous boundary.
  original_inspect = __import__("sqlalchemy").inspect
  inspector = original_inspect(eng)
  columns = inspector.get_columns("delegations")
  tables = inspector.get_table_names()
  class Schema:
    def get_columns(self, _name):
      return columns
    def get_table_names(self):
      return tables
  monkeypatch.setattr("sqlalchemy.inspect", lambda _engine: Schema())
  monkeypatch.setattr(eng, "raw_connection", FailingConnection)
  with pytest.raises(RuntimeError, match="interrupted replacement"):
    _allow_chat_owned_delegations(eng)
  monkeypatch.setattr(eng, "raw_connection", real_connection)
  with eng.connect() as conn:
    assert conn.execute(text("SELECT * FROM delegations")).one() == ("old", 7)
    assert conn.execute(text("PRAGMA foreign_keys")).scalar() == before
    assert conn.execute(text("SELECT name FROM sqlite_master WHERE name='delegations__core_0074'")).first() is None
