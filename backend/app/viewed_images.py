"""Chat-owned snapshots of the images an agent views.

A viewed path names a mutable file (often under the global ``/tmp``), so it
cannot prove which bytes the provider saw. The image tool therefore reads the
file once and returns the bytes to the provider as its result. When the call
completes, the runner stores that very payload under the viewing chat's media
as an immutable content-addressed snapshot, and the owner's preview serves it.

Standard library only (Pillow is used when present): the control MCP server loads this file directly rather
than importing the backend application.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_VIEWED_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MODEL_EDGE = 2048
_SIGNATURES = (
  (b"\x89PNG\r\n\x1a\n", "image/png"),
  (b"\xff\xd8\xff", "image/jpeg"),
  (b"GIF87a", "image/gif"),
  (b"GIF89a", "image/gif"),
)
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
SNAPSHOT_NAME = re.compile(r"^viewed-[0-9a-f]{64}\.(?:png|jpg|gif|webp)$")


def image_type(data: bytes) -> str | None:
  """The raster type the bytes really are, never the type the name claims."""
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


def _fit_for_model(data: bytes, mime: str) -> tuple[bytes, str]:
  """Shrink an oversized raster the way Codex's own viewer does.

  A long edge above ``MAX_MODEL_EDGE`` adds tokens and bytes the model cannot
  use. Pillow is optional here; any failure keeps the original bytes, which
  the size cap already bounds.
  """
  try:
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
      if max(image.size) <= MAX_MODEL_EDGE:
        return data, mime
      image.thumbnail((MAX_MODEL_EDGE, MAX_MODEL_EDGE))
      out = io.BytesIO()
      if mime == "image/jpeg":
        image.convert("RGB").save(out, "JPEG", quality=90)
        return out.getvalue(), mime
      image.save(out, "PNG")
      return out.getvalue(), "image/png"
  except Exception:
    return data, mime


def read_viewed_image(path: str) -> tuple[bytes, str]:
  """Read one regular raster image in a single bounded read, or raise ValueError.

  The descriptor is opened non-blocking and checked after opening, so a FIFO
  or device path is refused instead of holding the worker.
  """
  if not isinstance(path, str) or not os.path.isabs(path):
    raise ValueError("path must be an absolute file path")
  try:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
  except OSError as exc:
    raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc
  try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      raise ValueError(f"{path} is not a regular file")
    with os.fdopen(fd, "rb") as handle:
      fd = -1
      data = handle.read(MAX_VIEWED_IMAGE_BYTES + 1)
  except OSError as exc:
    raise ValueError(f"cannot read {path}: {exc.strerror or exc}") from exc
  finally:
    if fd >= 0:
      os.close(fd)
  if len(data) > MAX_VIEWED_IMAGE_BYTES:
    raise ValueError("image is larger than 20 MB")
  mime = image_type(data)
  if mime is None:
    raise ValueError(f"{path} is not a PNG, JPEG, GIF, or WebP image")
  return _fit_for_model(data, mime)


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


def _payload_images(result: Any) -> list[dict]:
  content = result.get("content") if isinstance(result, dict) else None
  return [
    block for block in content or ()
    if isinstance(block, dict) and block.get("type") == "image"
  ]


def snapshot_result(
  data_dir: str | os.PathLike, chat_id: str, result: Any,
) -> str:
  """Store the image the provider received as this chat's snapshot; name it.

  ``result`` is the tool's MCP result. The bytes are decoded and hashed once
  from that payload, so the snapshot is exactly what the model saw, whatever
  happens to the viewed path afterwards. Anything that is not one valid image
  yields ``""`` and the view has no preview.
  """
  images = _payload_images(result)
  if len(images) != 1:
    return ""
  try:
    data = base64.b64decode(images[0].get("data") or "", validate=True)
  except (ValueError, TypeError):
    return ""
  mime = image_type(data)
  if mime is None or mime != images[0].get("mimeType"):
    return ""
  try:
    return store_snapshot(chat_media_dir(data_dir, chat_id), data, mime)
  except OSError:
    return ""


def without_image_data(result: Any) -> Any:
  """The result with each image's base64 replaced, for the stored transcript.

  The snapshot already holds the bytes, so keeping them in the tool output
  would store every view twice.
  """
  if not _payload_images(result):
    return result
  return {**result, "content": [
    {**block, "data": ""}
    if isinstance(block, dict) and block.get("type") == "image" else block
    for block in result["content"]
  ]}
