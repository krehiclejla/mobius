"""Chat-owned snapshots of the images a Codex agent views.

Codex's native image viewer reports only a path, and a path (often under the
global ``/tmp``) is mutable and shared, so it cannot prove which bytes the
model saw. Codex does record that proof: the view call's output, holding the
exact image it sent to the model (after its own downscaling), is appended to
the thread's rollout. That record is written after the view item completes and
before the turn completes, so the runner binds a turn's views once the turn
ends. It reads only the rollout lines this turn appended, stores each view's
recorded image under the viewing chat's media as an immutable
content-addressed snapshot, and the owner's preview serves that snapshot.
"""

from __future__ import annotations

import base64
import binascii
import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_SIGNATURES = (
  (b"\x89PNG\r\n\x1a\n", "image/png"),
  (b"\xff\xd8\xff", "image/jpeg"),
  (b"GIF87a", "image/gif"),
  (b"GIF89a", "image/gif"),
)
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
SNAPSHOT_NAME = re.compile(r"^viewed-[0-9a-f]{64}\.(?:png|jpg|gif|webp)$")
_THREAD_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_DATA_URL = re.compile(r"^data:(image/[a-z]+);base64,")
# Cheap pre-filter: only output records are parsed, never every turn line.
_OUTPUT_MARKER = b'"function_call_output"'


def image_type(data: bytes) -> str | None:
  """The raster type the bytes really are, never the type a name claims."""
  for signature, mime in _SIGNATURES:
    if data.startswith(signature):
      return mime
  if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
    return "image/webp"
  return None


def snapshot_name(data: bytes, mime: str) -> str:
  return f"viewed-{hashlib.sha256(data).hexdigest()}.{_EXTENSIONS[mime]}"


def chat_media_dir(data_dir: str | os.PathLike, chat_id: str) -> Path:
  return Path(data_dir) / "chats" / chat_id / "media"


def store_snapshot(media_dir: Path, data: bytes, mime: str) -> str:
  """Publish the bytes once under their digest; an existing name is the same image.

  The temporary file is linked into place, so a reader never observes a
  partial snapshot and no writer can replace a published one.
  """
  name = snapshot_name(data, mime)
  media_dir.mkdir(parents=True, exist_ok=True)
  target = media_dir / name
  if target.is_file():
    return name
  temporary = media_dir / f".{name}.{os.getpid()}.tmp"
  try:
    with open(temporary, "xb") as handle:
      handle.write(data)
    try:
      os.link(temporary, target)
    except FileExistsError:
      pass
  finally:
    temporary.unlink(missing_ok=True)
  return name


def rollout_path(codex_home: str | os.PathLike, thread_id: str) -> Path | None:
  """Codex writes ``sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl``."""
  if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
    return None
  matches = glob.glob(str(
    Path(codex_home) / "sessions" / "*" / "*" / "*" / f"rollout-*-{thread_id}.jsonl"
  ))
  return Path(matches[0]) if len(matches) == 1 else None


@dataclass(frozen=True)
class TurnMark:
  """Where one turn's records begin in its thread's rollout.

  A thread's first turn may create the rollout, so a missing file marks the
  start of the file it later becomes.
  """
  codex_home: str
  thread_id: str
  path: Path | None
  offset: int

  @classmethod
  def capture(cls, codex_home: str | os.PathLike, thread_id: str) -> "TurnMark":
    path = rollout_path(codex_home, thread_id)
    offset = 0
    if path is not None:
      try:
        offset = path.stat().st_size
      except OSError:
        path = None
    return cls(str(codex_home), thread_id, path, offset)

  def turn_records(self) -> Iterable[bytes]:
    """The rollout lines appended since the mark, filtered to call outputs."""
    path = self.path or rollout_path(self.codex_home, self.thread_id)
    if path is None:
      return
    offset = self.offset if self.path is not None else 0
    with open(path, "rb") as handle:
      if os.fstat(handle.fileno()).st_size < offset:
        return  # the file was replaced; its new lines cannot be attributed
      handle.seek(offset)
      for line in handle:
        if line.endswith(b"\n") and _OUTPUT_MARKER in line:
          yield line


def _recorded_image(output: object) -> tuple[bytes, str] | None:
  """The one image a view's recorded output carried to the model."""
  if not isinstance(output, list):
    return None
  urls = [
    part.get("image_url") for part in output
    if isinstance(part, dict) and part.get("type") == "input_image"
  ]
  if len(urls) != 1 or not isinstance(urls[0], str):
    return None
  header = _DATA_URL.match(urls[0])
  if header is None:
    return None
  try:
    data = base64.b64decode(urls[0][header.end():], validate=True)
  except (binascii.Error, ValueError):
    return None
  mime = image_type(data)
  return (data, mime) if mime is not None and mime == header.group(1) else None


def recorded_view_images(
  mark: TurnMark, call_ids: Iterable[str],
) -> dict[str, tuple[bytes, str]]:
  """Each view's image exactly as this turn's rollout recorded it."""
  wanted = set(call_ids)
  found: dict[str, tuple[bytes, str]] = {}
  for line in mark.turn_records():
    try:
      record = json.loads(line)
    except ValueError:
      continue
    payload = record.get("payload") if isinstance(record, dict) else None
    if (
      record.get("type") != "response_item"
      or not isinstance(payload, dict)
      or payload.get("type") != "function_call_output"
    ):
      continue
    call_id = payload.get("call_id")
    if call_id in wanted and call_id not in found:
      image = _recorded_image(payload.get("output"))
      if image is not None:
        found[call_id] = image
    if len(found) == len(wanted):
      break
  return found


def bind_turn_views(
  data_dir: str | os.PathLike,
  chat_id: str,
  mark: TurnMark,
  call_ids: Iterable[str],
) -> dict[str, str]:
  """Store this turn's viewed images as chat snapshots; name each by call.

  A view whose recorded image is missing or invalid gets no snapshot and so
  no served preview. Failing to read or write never fails the turn.
  """
  try:
    images = recorded_view_images(mark, call_ids)
    media = chat_media_dir(data_dir, chat_id)
    return {
      call_id: store_snapshot(media, data, mime)
      for call_id, (data, mime) in images.items()
    }
  except OSError:
    return {}
