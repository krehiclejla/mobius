import os
import sqlite3
import time

import pytest

from app import codex_store_compaction as compaction
from app.codex_session_lock import acquire_codex_session_activity, codex_home_in_use
from app.provider_session_retention import sweep_stale_provider_sessions

NOW = 1_790_000_000.0
DAY = 86400


def _codex_db(path, ddl):
  conn = sqlite3.connect(path)
  conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
  conn.execute("PRAGMA journal_mode=WAL")
  for statement in ddl:
    conn.execute(statement)
  conn.commit()
  return conn


def _logs(home, ages_days):
  conn = _codex_db(home / "logs_2.sqlite", [
    "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,"
    " feedback_log_body TEXT)",
    "CREATE INDEX idx_logs_ts ON logs(ts DESC, id DESC)",
  ])
  conn.executemany(
    "INSERT INTO logs (ts, feedback_log_body) VALUES (?, ?)",
    [(int(NOW - age * DAY), "x" * 2000) for age in ages_days],
  )
  conn.commit()
  conn.close()


def _history(home, threads):
  """threads: {thread_id: (updated_days_ago, rollout_exists)}"""
  sessions = home / "sessions"
  sessions.mkdir(exist_ok=True)
  state = _codex_db(home / "state_5.sqlite", [
    "CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,"
    " updated_at INTEGER NOT NULL)",
  ])
  history = _codex_db(home / "thread_history_1.sqlite", [
    "CREATE TABLE thread_items (thread_id TEXT NOT NULL, item_json TEXT NOT NULL)",
    "CREATE TABLE thread_turns (thread_id TEXT NOT NULL, turn_id TEXT NOT NULL)",
    "CREATE TABLE thread_history_projection_state (thread_id TEXT PRIMARY KEY,"
    " next_rollout_byte_offset INTEGER NOT NULL)",
  ])
  for thread_id, (age, exists) in threads.items():
    rollout = sessions / f"rollout-{thread_id}.jsonl"
    if exists:
      rollout.write_text("{}\n")
    state.execute(
      "INSERT INTO threads VALUES (?, ?, ?)",
      (thread_id, str(rollout), int(NOW - age * DAY)),
    )
    history.executemany(
      "INSERT INTO thread_items VALUES (?, ?)", [(thread_id, "y" * 4000)] * 50,
    )
    history.execute("INSERT INTO thread_turns VALUES (?, 't')", (thread_id,))
    history.execute(
      "INSERT INTO thread_history_projection_state VALUES (?, 0)", (thread_id,),
    )
  state.commit()
  history.commit()
  state.close()
  history.close()


def _count(path, sql, *args):
  conn = sqlite3.connect(path)
  try:
    return conn.execute(sql, args).fetchone()[0]
  finally:
    conn.close()


def _compact(home, budget=None):
  return compaction.compact_codex_stores(
    home, now=NOW, rollout_cutoff=NOW - 14 * DAY, budget_seconds=budget,
  )


def test_log_trim_keeps_two_days_and_returns_the_space(tmp_path):
  _logs(tmp_path, [0.5, 1.5] + [5] * 3000)
  before = os.path.getsize(tmp_path / "logs_2.sqlite")

  result = _compact(tmp_path)

  assert result["logs"]["status"] == "completed"
  assert result["logs"]["deleted_rows"] == 3000
  assert _count(tmp_path / "logs_2.sqlite", "SELECT count(*) FROM logs") == 2
  assert os.path.getsize(tmp_path / "logs_2.sqlite") < before / 10
  assert result["logs"]["reclaimed_bytes"] > 0
  assert _count(tmp_path / "logs_2.sqlite", "PRAGMA auto_vacuum") == 2
  conn = sqlite3.connect(tmp_path / "logs_2.sqlite")
  assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
  assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
  conn.close()


def test_history_is_dropped_only_for_stale_threads_whose_rollout_is_gone(tmp_path):
  _history(tmp_path, {
    "retired": (20, False),
    "stale-but-resumable": (20, True),
    "recent-without-rollout": (3, False),
  })

  result = _compact(tmp_path)

  assert result["thread_history"]["dropped_threads"] == 1
  items = tmp_path / "thread_history_1.sqlite"
  for table in ("thread_items", "thread_turns", "thread_history_projection_state"):
    assert _count(items, f"SELECT count(*) FROM {table} WHERE thread_id='retired'") == 0
  for kept in ("stale-but-resumable", "recent-without-rollout"):
    assert _count(items, "SELECT count(*) FROM thread_items WHERE thread_id=?", kept) == 50
  # The thread index itself keeps every row; only free pages are released.
  assert _count(tmp_path / "state_5.sqlite", "SELECT count(*) FROM threads") == 3


def test_unfamiliar_codex_layout_is_left_untouched(tmp_path):
  conn = _codex_db(tmp_path / "logs_3.sqlite", [
    "CREATE TABLE logs (id INTEGER PRIMARY KEY, created TEXT)",
  ])
  conn.execute("INSERT INTO logs (created) VALUES ('old')")
  conn.commit()
  conn.close()

  result = _compact(tmp_path)

  assert result["logs"]["status"] == "schema_unrecognized"
  assert _count(tmp_path / "logs_3.sqlite", "SELECT count(*) FROM logs") == 1
  assert result["thread_history"]["status"] == "absent"


def test_only_the_current_log_generation_is_compacted(tmp_path):
  _logs(tmp_path, [5] * 10)
  (tmp_path / "logs_1.sqlite").write_bytes(b"superseded, not ours to rewrite")

  _compact(tmp_path)

  assert (tmp_path / "logs_1.sqlite").read_bytes() == b"superseded, not ours to rewrite"
  assert _count(tmp_path / "logs_2.sqlite", "SELECT count(*) FROM logs") == 0


def test_a_busy_store_is_skipped_without_failing_the_sweep(tmp_path, monkeypatch):
  monkeypatch.setattr(compaction, "_BUSY_TIMEOUT_MS", 50)
  _logs(tmp_path, [5] * 10)
  holder = sqlite3.connect(tmp_path / "logs_2.sqlite", isolation_level=None)
  holder.execute("BEGIN EXCLUSIVE")
  try:
    result = _compact(tmp_path)
  finally:
    holder.execute("ROLLBACK")
    holder.close()

  assert result["logs"]["status"] == "busy"
  assert result["complete"] is False
  assert _count(tmp_path / "logs_2.sqlite", "SELECT count(*) FROM logs") == 10


def test_an_exhausted_budget_leaves_work_for_the_next_sweep(tmp_path):
  _logs(tmp_path, [5] * 100)
  _history(tmp_path, {"retired": (20, False)})

  first = _compact(tmp_path, budget=0)
  assert first["complete"] is False
  assert _count(tmp_path / "logs_2.sqlite", "SELECT count(*) FROM logs") == 100

  second = _compact(tmp_path)
  assert second["complete"] is True
  assert _count(tmp_path / "logs_2.sqlite", "SELECT count(*) FROM logs") == 0
  assert second["thread_history"]["dropped_threads"] == 1


def test_scratch_cleanup_is_limited_to_stale_temp_and_cache_entries(tmp_path):
  old = time.time() - 3 * DAY
  for name in ("tmp", ".tmp", "cache", "generated_images", "sessions"):
    (tmp_path / name / "stale").mkdir(parents=True)
    os.utime(tmp_path / name / "stale", (old, old))
  (tmp_path / "cache" / "fresh.json").write_text("{}")
  (tmp_path / "auth.json").write_text("{}")
  os.utime(tmp_path / "auth.json", (old, old))
  outside = tmp_path.parent / "outside-scratch"
  (outside / "stale").mkdir(parents=True)
  os.utime(outside / "stale", (old, old))
  os.rmdir(tmp_path / "tmp" / "stale")
  os.rmdir(tmp_path / "tmp")
  (tmp_path / "tmp").symlink_to(outside, target_is_directory=True)

  result = compaction.compact_codex_stores(
    tmp_path, now=time.time(), rollout_cutoff=0, budget_seconds=None,
  )

  assert result["scratch"]["removed_entries"] == 2
  assert not (tmp_path / ".tmp" / "stale").exists()
  assert not (tmp_path / "cache" / "stale").exists()
  assert (tmp_path / "cache" / "fresh.json").exists()
  assert (tmp_path / "generated_images" / "stale").exists()
  assert (tmp_path / "sessions" / "stale").exists()
  assert (tmp_path / "auth.json").exists()
  assert (outside / "stale").exists()


def test_sweep_compacts_stores_only_while_no_codex_process_holds_the_lock(tmp_path):
  home = tmp_path / "cli-auth" / "codex"
  home.mkdir(parents=True)
  _logs(home, [5] * 20)

  activity = acquire_codex_session_activity(tmp_path)
  try:
    skipped = sweep_stale_provider_sessions(tmp_path, now=NOW)
  finally:
    activity.release()
  assert skipped["status"] == "skipped_active"
  assert _count(home / "logs_2.sqlite", "SELECT count(*) FROM logs") == 20

  swept = sweep_stale_provider_sessions(tmp_path, now=NOW)
  assert swept["status"] == "completed"
  assert swept["store_reclaimed_bytes"] > 0
  assert _count(home / "logs_2.sqlite", "SELECT count(*) FROM logs") == 0


def test_store_failure_never_breaks_rollout_retention(tmp_path, monkeypatch):
  home = tmp_path / "cli-auth" / "codex"
  home.mkdir(parents=True)

  def broken(*_args, **_kwargs):
    raise RuntimeError("unexpected")

  monkeypatch.setattr(compaction, "compact_codex_stores", broken)
  result = sweep_stale_provider_sessions(tmp_path, now=NOW)

  assert result["status"] == "completed"
  assert result["stores"]["status"] == "failed"
  assert result["store_reclaimed_bytes"] == 0


def test_a_spent_budget_never_starts_a_rebuild(tmp_path, monkeypatch):
  _history(tmp_path, {"kept": (1, True)})
  conn = sqlite3.connect(tmp_path / "state_5.sqlite")
  conn.execute("CREATE TABLE filler (x TEXT)")
  conn.executemany("INSERT INTO filler VALUES (?)", [("z" * 4000,)] * 500)
  conn.commit()
  conn.execute("DROP TABLE filler")
  conn.commit()
  conn.close()
  statements = []
  real_connect = compaction._connect

  def recording_connect(path):
    conn = real_connect(path)
    conn.set_trace_callback(statements.append)
    return conn

  monkeypatch.setattr(compaction, "_connect", recording_connect)
  spent = _compact(tmp_path, budget=0)
  assert "VACUUM" not in statements
  assert spent["state"]["status"] == "incomplete"

  statements.clear()
  resumed = _compact(tmp_path)
  assert "VACUUM" in statements
  assert resumed["state"]["status"] == "completed"


def test_an_open_codex_file_marks_codex_busy_even_without_the_launcher_lock(tmp_path):
  home = tmp_path / "cli-auth" / "codex"
  (home / "sessions").mkdir(parents=True)
  rollout = home / "sessions" / "rollout-cli.jsonl"
  rollout.write_text("{}")

  assert codex_home_in_use(tmp_path) is False
  with rollout.open():
    assert codex_home_in_use(tmp_path) is True
  assert codex_home_in_use(tmp_path) is False


def test_sweep_leaves_codex_alone_while_an_unlocked_codex_holds_its_files(tmp_path):
  """A Codex CLI started from an agent shell takes no Möbius lock."""
  home = tmp_path / "cli-auth" / "codex"
  home.mkdir(parents=True)
  _logs(home, [5] * 20)

  with (home / "logs_2.sqlite").open("rb"):
    busy = sweep_stale_provider_sessions(tmp_path, now=NOW)
  assert busy["status"] == "skipped_active"
  assert _count(home / "logs_2.sqlite", "SELECT count(*) FROM logs") == 20

  idle = sweep_stale_provider_sessions(tmp_path, now=NOW)
  assert idle["status"] == "completed"
  assert _count(home / "logs_2.sqlite", "SELECT count(*) FROM logs") == 0
