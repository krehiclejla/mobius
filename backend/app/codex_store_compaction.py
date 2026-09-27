"""Keep Codex's local SQLite stores and scratch space bounded.

Codex (0.157) prunes diagnostic log rows after ten days but never releases the
freed pages (openai/codex#35823), keeps thread history for sessions whose
rollout files Möbius already retired, and never reclaims free pages in its
thread index. On a metered volume that grew to several gigabytes.

This runs only inside the Codex retention sweep while it holds the exclusive
side of the cross-process Codex lock, so no Codex process has these databases
open. It never touches authentication, configuration, rollout files, or any
path outside CODEX_HOME. Each step checks the schema it expects and skips an
unfamiliar layout or a busy database, so a Codex upgrade degrades to a no-op
instead of a guess. Work is batched with WAL checkpoints between batches (so
the WAL never holds a whole deletion) and stops at a deadline; the next sweep
resumes where it left off.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
import time
from pathlib import Path

# Diagnostic rows only feed Codex's own feedback uploads.
LOG_RETENTION_SECONDS = 2 * 86400
# Codex's temp folders and download cache; entries older than this are stale.
SCRATCH_DIRS = ("tmp", ".tmp", "cache")
SCRATCH_MIN_AGE_SECONDS = 86400

_DELETE_BATCH_ROWS = 20_000
_THREADS_PER_COMMIT = 25
_VACUUM_PAGES_PER_STEP = 16384
_BUSY_TIMEOUT_MS = 2000


class _Deadline:
  def __init__(self, seconds: float | None):
    self._at = None if seconds is None else time.monotonic() + seconds

  def passed(self) -> bool:
    return self._at is not None and time.monotonic() >= self._at


def _current_generation(home: Path, family: str) -> Path | None:
  pattern = re.compile(rf"^{family}_(\d+)\.sqlite$")
  best: tuple[int, Path] | None = None
  try:
    entries = list(os.scandir(home))
  except OSError:
    return None
  for entry in entries:
    match = pattern.match(entry.name)
    if match and entry.is_file(follow_symlinks=False):
      generation = int(match.group(1))
      if best is None or generation > best[0]:
        best = (generation, Path(entry.path))
  return best[1] if best else None


def _footprint(path: Path) -> int:
  total = 0
  for suffix in ("", "-wal"):
    try:
      total += os.lstat(f"{path}{suffix}").st_size
    except OSError:
      pass
  return total


def _connect(path: Path) -> sqlite3.Connection:
  conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
  conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
  return conn


def _has_columns(conn: sqlite3.Connection, table: str, columns: set[str]) -> bool:
  found = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
  return columns <= found


def _checkpoint(conn: sqlite3.Connection) -> None:
  conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _release_free_pages(
  conn: sqlite3.Connection, path: Path, deadline: _Deadline,
) -> bool:
  """Return pages to the filesystem; False when the database cannot do it.

  Incremental vacuum visits every trailing page, so its cost follows the file
  size; a rebuild's cost follows the live data. After a large retention delete
  (a log that is 97% free pages) the rebuild is an order of magnitude faster,
  and it needs only live-sized temporary space, which is checked first.
  """
  if conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
    return False
  free = conn.execute("PRAGMA freelist_count").fetchone()[0]
  total = conn.execute("PRAGMA page_count").fetchone()[0]
  page_size = conn.execute("PRAGMA page_size").fetchone()[0]
  live_bytes = (total - free) * page_size
  if (
    not deadline.passed()
    and free * 2 > total
    and shutil.disk_usage(path.parent).free > 3 * live_bytes
  ):
    conn.execute("VACUUM")
    _checkpoint(conn)
    return True
  while conn.execute("PRAGMA freelist_count").fetchone()[0] and not deadline.passed():
    conn.execute(f"PRAGMA incremental_vacuum({_VACUUM_PAGES_PER_STEP})").fetchall()
    _checkpoint(conn)
  return True


def _compact(path: Path | None, deadline: _Deadline, work) -> dict:
  if path is None:
    return {"status": "absent"}
  started = time.monotonic()
  before = _footprint(path)
  try:
    conn = _connect(path)
  except sqlite3.Error as exc:
    return {"status": "unavailable", "error": type(exc).__name__}
  try:
    outcome = work(conn)
    if outcome.get("status") == "completed" and not _release_free_pages(
      conn, path, deadline,
    ):
      outcome["vacuum"] = "unavailable"
    _checkpoint(conn)
    if deadline.passed() and outcome.get("status") == "completed":
      outcome["status"] = "incomplete"
  except sqlite3.OperationalError as exc:
    outcome = {"status": "busy", "error": str(exc)[:120]}
  finally:
    conn.close()
  outcome["reclaimed_bytes"] = max(0, before - _footprint(path))
  outcome["duration_ms"] = round((time.monotonic() - started) * 1000)
  return outcome


def _trim_logs(conn: sqlite3.Connection, *, now: float, deadline: _Deadline) -> dict:
  if not _has_columns(conn, "logs", {"id", "ts"}):
    return {"status": "schema_unrecognized"}
  cutoff = int(now - LOG_RETENTION_SECONDS)
  deleted = 0
  while not deadline.passed():
    conn.execute("BEGIN IMMEDIATE")
    removed = conn.execute(
      "DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE ts < ? LIMIT ?)",
      (cutoff, _DELETE_BATCH_ROWS),
    ).rowcount
    conn.execute("COMMIT")
    _checkpoint(conn)
    deleted += removed
    if removed < _DELETE_BATCH_ROWS:
      return {"status": "completed", "deleted_rows": deleted}
  return {"status": "incomplete", "deleted_rows": deleted}


_THREAD_HISTORY_TABLES = (
  "thread_items", "thread_turns", "thread_realtime_items",
  "thread_history_projection_state",
)


def _retired_threads(state: Path | None, rollout_cutoff: float) -> list[str] | None:
  """Threads Codex can no longer resume: stale and without a rollout file."""
  if state is None:
    return None
  conn = _connect(state)
  try:
    if not _has_columns(conn, "threads", {"id", "rollout_path", "updated_at"}):
      return None
    rows = conn.execute(
      "SELECT id, rollout_path FROM threads WHERE updated_at < ?",
      (int(rollout_cutoff),),
    ).fetchall()
  finally:
    conn.close()
  return [
    thread_id for thread_id, rollout in rows
    if isinstance(rollout, str) and rollout and not os.path.lexists(rollout)
  ]


def _drop_retired_history(
  conn: sqlite3.Connection, retired: list[str] | None, *, deadline: _Deadline,
) -> dict:
  if retired is None:
    return {"status": "schema_unrecognized"}
  tables = [
    table for table in _THREAD_HISTORY_TABLES
    if _has_columns(conn, table, {"thread_id"})
  ]
  if "thread_items" not in tables:
    return {"status": "schema_unrecognized"}
  present = {
    row[0] for row in conn.execute("SELECT DISTINCT thread_id FROM thread_items")
  }
  targets = [thread_id for thread_id in retired if thread_id in present]
  dropped = 0
  for start in range(0, len(targets), _THREADS_PER_COMMIT):
    if deadline.passed():
      return {"status": "incomplete", "dropped_threads": dropped}
    batch = targets[start:start + _THREADS_PER_COMMIT]
    conn.execute("BEGIN IMMEDIATE")
    for thread_id in batch:
      for table in tables:
        conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
    conn.execute("COMMIT")
    _checkpoint(conn)
    dropped += len(batch)
  return {"status": "completed", "dropped_threads": dropped}


def _tree_bytes(path: Path) -> int:
  info = path.lstat()
  if not stat.S_ISDIR(info.st_mode):
    return info.st_blocks * 512
  total = 0
  for base, dirs, files in os.walk(path, followlinks=False):
    for name in files + dirs:
      try:
        total += os.lstat(os.path.join(base, name)).st_blocks * 512
      except OSError:
        pass
  return total


def _clear_stale_scratch(home: Path, *, now: float) -> dict:
  removed = reclaimed = errors = 0
  for name in SCRATCH_DIRS:
    root = home / name
    if root.is_symlink() or not root.is_dir():
      continue
    for entry in list(os.scandir(root)):
      path = Path(entry.path)
      try:
        info = path.lstat()
        if now - info.st_mtime < SCRATCH_MIN_AGE_SECONDS:
          continue
        size = _tree_bytes(path)
        if stat.S_ISDIR(info.st_mode):
          shutil.rmtree(path)
        else:
          path.unlink()
        removed += 1
        reclaimed += size
      except FileNotFoundError:
        continue
      except OSError:
        errors += 1
  return {"removed_entries": removed, "reclaimed_bytes": reclaimed, "errors": errors}


def compact_codex_stores(
  codex_home: str | Path,
  *,
  now: float,
  rollout_cutoff: float,
  budget_seconds: float | None,
) -> dict:
  """Bound Codex's log, thread history, thread index, and scratch space.

  The caller must hold the exclusive Codex sweep lock. ``rollout_cutoff`` is
  the rollout retention boundary: history is dropped only for threads last
  updated before it whose rollout file is already gone.
  """
  home = Path(codex_home)
  deadline = _Deadline(budget_seconds)
  state = _current_generation(home, "state")
  result = {
    "scratch": _clear_stale_scratch(home, now=now),
    "logs": _compact(
      _current_generation(home, "logs"), deadline,
      lambda conn: _trim_logs(conn, now=now, deadline=deadline),
    ),
  }
  try:
    retired = _retired_threads(state, rollout_cutoff)
  except sqlite3.Error as exc:
    result["thread_history"] = {"status": "busy", "error": str(exc)[:120]}
  else:
    result["thread_history"] = _compact(
      _current_generation(home, "thread_history"), deadline,
      lambda conn: _drop_retired_history(conn, retired, deadline=deadline),
    )
  result["state"] = _compact(
    state, deadline, lambda conn: {"status": "completed"},
  )
  result["reclaimed_bytes"] = sum(
    part.get("reclaimed_bytes", 0) for part in result.values()
    if isinstance(part, dict)
  )
  result["complete"] = all(
    result[name].get("status") in ("completed", "absent")
    for name in ("logs", "thread_history", "state")
  )
  return result
