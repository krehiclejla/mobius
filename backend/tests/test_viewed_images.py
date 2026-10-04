"""A viewed image previews the chat snapshot of exactly what the provider saw."""

import base64
import importlib.util
import io
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

from app import viewed_images
from app.codex_events import _is_control_image_view, _tool_start_event
from app.codex_sdk_runner import _codex_config_overrides
from app.config import get_settings
from app.events import process_event

PNG = b"\x89PNG\r\n\x1a\n" + b"first image"
OTHER_PNG = b"\x89PNG\r\n\x1a\n" + b"second image"


def _control(monkeypatch, chat_id):
  path = Path(__file__).resolve().parents[1] / "scripts" / "mobius_control_mcp.py"
  spec = importlib.util.spec_from_file_location("mobius_control_view_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  monkeypatch.setenv("MOBIUS_IMAGE_VIEWER", "1")
  monkeypatch.setenv("MOBIUS_RUN_TOKEN", "run")
  monkeypatch.setenv("CHAT_ID", chat_id)
  return module


def _view(control, monkeypatch, chat_id, path):
  monkeypatch.setenv("CHAT_ID", chat_id)
  return control._call_tool({"name": "view_image", "arguments": {"path": str(path)}})


def _bound(chat_id, result):
  return viewed_images.snapshot_result(get_settings().data_dir, chat_id, result)


def _served(client, auth, chat_id, name):
  return client.get(f"/api/chats/{chat_id}/media/{name}", headers=auth)


def test_the_preview_serves_the_bytes_returned_to_the_provider(
  monkeypatch, tmp_path, client, auth, chat,
):
  source = tmp_path / "render.png"
  source.write_bytes(PNG)
  control = _control(monkeypatch, chat.id)

  result = _view(control, monkeypatch, chat.id, source)

  image, note = result["content"]
  assert result["isError"] is False
  assert base64.b64decode(image["data"]) == PNG
  assert image["mimeType"] == "image/png"
  assert str(source) in note["text"]
  name = _bound(chat.id, result)
  assert viewed_images.SNAPSHOT_NAME.fullmatch(name)
  assert _served(client, auth, chat.id, name).content == PNG


def test_overwriting_the_path_after_the_view_keeps_its_preview(
  monkeypatch, tmp_path, client, auth, chat,
):
  source = tmp_path / "render.png"
  source.write_bytes(PNG)
  control = _control(monkeypatch, chat.id)
  first = _view(control, monkeypatch, chat.id, source)

  source.write_bytes(OTHER_PNG)
  second = _view(control, monkeypatch, chat.id, source)

  assert _served(client, auth, chat.id, _bound(chat.id, first)).content == PNG
  assert _served(client, auth, chat.id, _bound(chat.id, second)).content == OTHER_PNG


def test_each_chat_stores_and_serves_only_its_own_payload(
  monkeypatch, tmp_path, client, auth, chat,
):
  source = tmp_path / "shared-name.png"
  other_chat = str(uuid.uuid4())
  control = _control(monkeypatch, chat.id)
  source.write_bytes(PNG)
  mine = _view(control, monkeypatch, chat.id, source)
  source.write_bytes(OTHER_PNG)
  theirs = _view(control, monkeypatch, other_chat, source)

  name = _bound(chat.id, mine)
  other_name = _bound(other_chat, theirs)
  assert name != other_name
  assert _served(client, auth, chat.id, name).content == PNG
  # The other chat's snapshot is not in this chat's media, so its name does
  # not resolve here.
  assert _served(client, auth, chat.id, other_name).status_code == 404


def test_a_non_image_is_refused_and_leaves_no_snapshot(monkeypatch, tmp_path, chat):
  source = tmp_path / "notes.png"
  source.write_text("private text with an image name", encoding="utf-8")
  control = _control(monkeypatch, chat.id)

  result = _view(control, monkeypatch, chat.id, source)

  assert result["isError"] is True
  assert "not a PNG, JPEG, GIF, or WebP image" in result["content"][0]["text"]
  media = viewed_images.chat_media_dir(get_settings().data_dir, chat.id)
  assert not list(media.glob("viewed-*"))


def test_viewing_writes_no_snapshot_itself(monkeypatch, tmp_path, chat):
  source = tmp_path / "render.png"
  source.write_bytes(PNG)
  control = _control(monkeypatch, chat.id)

  _view(control, monkeypatch, chat.id, source)

  media = viewed_images.chat_media_dir(get_settings().data_dir, chat.id)
  assert not list(media.glob("viewed-*"))


def test_a_fifo_path_is_refused_without_blocking(monkeypatch, tmp_path, chat):
  fifo = tmp_path / "pipe.png"
  os.mkfifo(fifo)
  control = _control(monkeypatch, chat.id)

  result = _view(control, monkeypatch, chat.id, fifo)

  assert result["isError"] is True
  assert "not a regular file" in result["content"][0]["text"]


def test_an_oversized_image_is_scaled_down_for_the_model(monkeypatch, tmp_path, chat):
  from PIL import Image
  source = tmp_path / "wide.png"
  Image.new("RGB", (5000, 100), "red").save(source)
  control = _control(monkeypatch, chat.id)

  image = _view(control, monkeypatch, chat.id, source)["content"][0]

  data = base64.b64decode(image["data"])
  assert viewed_images.image_type(data) == image["mimeType"] == "image/png"
  with Image.open(io.BytesIO(data)) as scaled:
    assert max(scaled.size) == viewed_images.MAX_MODEL_EDGE


def test_the_stored_transcript_result_omits_the_image_bytes():
  image = {"type": "image", "data": base64.b64encode(PNG).decode(), "mimeType": "image/png"}
  text = {"type": "text", "text": "Viewed /tmp/a.png."}
  result = {"content": [image, text]}

  stripped = viewed_images.without_image_data(result)

  assert stripped["content"] == [{**image, "data": ""}, text]
  assert result["content"][0]["data"]  # the provider's payload is untouched
  assert viewed_images.without_image_data(None) is None


def test_only_one_valid_matching_payload_is_stored(chat):
  media = viewed_images.chat_media_dir(get_settings().data_dir, chat.id)
  image = {"type": "image", "data": base64.b64encode(PNG).decode(), "mimeType": "image/png"}

  assert _bound(chat.id, {"content": [{**image, "mimeType": "image/gif"}]}) == ""
  assert _bound(chat.id, {"content": [image, image]}) == ""
  assert _bound(chat.id, {"content": [{**image, "data": "not base64!"}]}) == ""
  assert _bound(chat.id, {"content": [{**image, "data": ""}]}) == ""
  assert _bound(chat.id, None) == ""
  assert not list(media.glob("viewed-*"))

  name = _bound(chat.id, {"content": [image]})
  assert name == viewed_images.snapshot_name(PNG, "image/png")
  assert (media / name).read_bytes() == PNG
  assert _bound(chat.id, {"content": [image]}) == name


def test_a_completed_control_view_keeps_no_image_bytes_in_its_output():
  from app.codex_events import _tool_completed_events

  class McpCall(SimpleNamespace):
    pass

  payload = base64.b64encode(PNG).decode()
  result = {"content": [
    {"type": "image", "data": payload, "mimeType": "image/png"},
    {"type": "text", "text": "Viewed /tmp/a.png."},
  ]}
  item = McpCall(
    server="mobius_control", tool="view_image", status="completed",
    error=None, result=result,
  )

  sdk = {
    "McpToolCallThreadItem": McpCall,
    "CommandExecutionThreadItem": type("Command", (), {}),
    "FileChangeThreadItem": type("FileChange", (), {}),
  }
  events = _tool_completed_events(item, sdk)

  assert payload not in repr(events)
  assert "Viewed /tmp/a.png." in events[0]["content"]
  assert events[-1] == {"type": "tool_end"}


def test_view_image_is_offered_only_where_it_replaces_codexs_viewer(monkeypatch, chat):
  control = _control(monkeypatch, chat.id)
  assert "view_image" in control._available_tool_names()
  monkeypatch.delenv("MOBIUS_IMAGE_VIEWER")
  assert "view_image" not in control._available_tool_names()
  assert "features.view_image=false" in _codex_config_overrides()
  assert not any(o.startswith("tools.view_image") for o in _codex_config_overrides())


def test_a_control_view_renders_as_an_image_view_of_its_path():
  class McpCall(SimpleNamespace):
    pass

  sdk = {"McpToolCallThreadItem": McpCall}
  item = McpCall(server="mobius_control", tool="view_image", arguments={"path": "/tmp/a.png"})
  assert _tool_start_event(item, sdk) == {
    "type": "tool_start", "tool": "ViewImage", "input": "/tmp/a.png",
  }
  other = McpCall(server="elsewhere", tool="view_image", arguments={"path": "/tmp/a.png"})
  assert not _is_control_image_view(other, sdk)


def test_tool_end_keeps_only_a_snapshot_name_on_the_image_view():
  blocks = [
    {"type": "tool", "tool": "ViewImage", "status": "running", "tool_use_id": "image"},
    {"type": "tool", "tool": "Bash", "status": "running", "tool_use_id": "shell"},
  ]
  name = viewed_images.snapshot_name(PNG, "image/png")
  process_event({"type": "tool_end", "tool_use_id": "image", "viewed_image_media": name}, blocks)
  process_event({"type": "tool_end", "tool_use_id": "shell", "viewed_image_media": name}, blocks)
  assert blocks[0]["viewed_image_media"] == name
  assert "viewed_image_media" not in blocks[1]

  blocks[0]["status"] = "running"
  blocks[0].pop("viewed_image_media")
  process_event({
    "type": "tool_end", "tool_use_id": "image", "viewed_image_media": "../uploads/x.png",
  }, blocks)
  assert "viewed_image_media" not in blocks[0]
