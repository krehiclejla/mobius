"""Private, host-scoped durable Connect output ledger.

The host registry owns admission; this ledger owns acknowledged output and
finished identities. No automatic retention policy deletes command history.
"""
from __future__ import annotations

import json
from contextlib import closing
import os
import re
import sqlite3
from pathlib import Path

from app.config import get_settings

PAGE_CHARS = 512_000
PAGE_CHUNKS = 4096
_SAFE_HOST = re.compile(r"^h_[a-f0-9]{16}$")


def _path(host_id: str) -> Path:
  if not _SAFE_HOST.fullmatch(host_id):
    raise ValueError("Invalid host id")
  directory = Path(get_settings().data_dir) / "shared" / "connect" / "output"
  directory.mkdir(parents=True, exist_ok=True, mode=0o700)
  os.chmod(directory, 0o700)
  return directory / f"{host_id}.sqlite3"


def _open(host_id: str) -> sqlite3.Connection:
  path = _path(host_id)
  # Pre-create privately; changing process umask would race other threads.
  try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
  except FileExistsError:
    pass
  else:
    os.close(fd)
  db = sqlite3.connect(path, timeout=10)
  os.chmod(path, 0o600)
  db.execute("PRAGMA journal_mode=DELETE")
  db.execute("""CREATE TABLE IF NOT EXISTS chunks (
    request_id TEXT NOT NULL, seq INTEGER NOT NULL, stream TEXT NOT NULL,
    text TEXT NOT NULL, PRIMARY KEY(request_id, seq))""")
  db.execute("""CREATE TABLE IF NOT EXISTS finished (
    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
    finished_at REAL NOT NULL, output_seq INTEGER, result TEXT NOT NULL)""")
  db.execute("CREATE INDEX IF NOT EXISTS finished_recent ON finished(finished_at DESC,request_id DESC)")
  return db


def append(host_id: str, request_id: str, chunks: list[dict]) -> int:
  with closing(_open(host_id)) as db:
    with db:
      for c in chunks:
        existing = db.execute(
          "SELECT stream,text FROM chunks WHERE request_id=? AND seq=?",
          (request_id, c["seq"]),
        ).fetchone()
        if existing is not None:
          if existing != (c["stream"], c["text"]):
            raise ValueError("Conflicting output chunk replay")
          continue
        db.execute(
          "INSERT INTO chunks(request_id,seq,stream,text) VALUES (?,?,?,?)",
          (request_id, c["seq"], c["stream"], c["text"]),
        )
    row = db.execute(
      "SELECT MAX(seq) FROM chunks WHERE request_id=?", (request_id,),
    ).fetchone()
    return int(row[0]) + 1 if row[0] is not None else 0


def finish(host_id: str, request_id: str, fingerprint: str,
           finished_at: float, output_seq: int | None, result: dict) -> None:
  with closing(_open(host_id)) as db:
    with db:
      existing = db.execute(
        "SELECT fingerprint FROM finished WHERE request_id=?", (request_id,),
      ).fetchone()
      if existing is not None and existing[0] != fingerprint:
        raise ValueError("Conflicting finished command identity")
      db.execute(
        "INSERT OR IGNORE INTO finished "
        "(request_id,fingerprint,finished_at,output_seq,result) "
        "VALUES (?,?,?,?,?)",
        (request_id, fingerprint, finished_at, output_seq,
         json.dumps(result, ensure_ascii=False)),
      )


def finished(host_id: str, request_id: str) -> dict | None:
  with closing(_open(host_id)) as db:
    row = db.execute(
      "SELECT fingerprint,finished_at,output_seq,result FROM finished "
      "WHERE request_id=?", (request_id,),
    ).fetchone()
  return ({"fingerprint": row[0], "finished_at": row[1],
           "output_seq": row[2], "result": json.loads(row[3])}
          if row is not None else None)


def recent(host_id: str, limit: int = 20) -> list[dict]:
  with closing(_open(host_id)) as db:
    rows = db.execute(
      "SELECT request_id,finished_at,result FROM finished "
      "ORDER BY finished_at DESC,request_id DESC LIMIT ?",
      (max(0, min(limit, 100)),),
    ).fetchall()
  items = []
  for rid, finished_at, result in rows:
    terminal = json.loads(result)
    items.append({
      "id": rid, "finished_at": finished_at,
      "outcome": terminal.get("outcome"),
      "exit_code": terminal.get("exit_code"),
      "label": None,
    })
  return items


def page(host_id: str, request_id: str, after: int) -> dict:
  with closing(_open(host_id)) as db:
    rows = db.execute(
      "SELECT seq,stream,text FROM chunks WHERE request_id=? AND seq>=? "
      "ORDER BY seq LIMIT ?", (request_id, after, PAGE_CHUNKS + 1),
    )
    max_row = db.execute(
      "SELECT MAX(seq) FROM chunks WHERE request_id=?", (request_id,),
    ).fetchone()
    available_next = int(max_row[0]) + 1 if max_row[0] is not None else 0
    selected = []
    chars = 0
    for seq, stream, value in rows:
      if len(selected) >= PAGE_CHUNKS or (selected and chars + len(value) > PAGE_CHARS):
        break
      selected.append({"seq": seq, "stream": stream, "text": value})
      chars += len(value)
    next_cursor = selected[-1]["seq"] + 1 if selected else after
    return {"chunks": selected, "next": next_cursor,
            "available_next": available_next,
            "has_more": next_cursor < available_next}


def complete(host_id: str, request_id: str, expected: int | None) -> bool:
  if expected is None:
    return False
  with closing(_open(host_id)) as db:
    row = db.execute(
      "SELECT COUNT(*),MIN(seq),MAX(seq) FROM chunks "
      "WHERE request_id=? AND seq<?", (request_id, expected),
    ).fetchone()
  return expected == 0 or (row[0] == expected and row[1] == 0
                           and row[2] == expected - 1)


def delete_host(host_id: str) -> None:
  path = _path(host_id)
  path.unlink(missing_ok=True)
