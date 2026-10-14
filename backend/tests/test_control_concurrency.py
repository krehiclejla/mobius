"""The stdio transport runs tools concurrently without sharing caller identity."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mobius_control_mcp.py"


def _control():
  spec = importlib.util.spec_from_file_location("control_concurrency_test", SCRIPT)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _call(request_id, name, arguments=None):
  return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
          "params": {"name": name, "arguments": arguments or {}}}


def _serve(control, messages):
  output = io.StringIO()
  control.serve(io.StringIO("".join(json.dumps(item) + "\n" for item in messages)), output)
  return [json.loads(line) for line in output.getvalue().splitlines()]


def test_slow_tool_does_not_block_ping_and_caller_env_isolated(tmp_path, monkeypatch):
  slow_started = threading.Event()
  fast_finished = threading.Event()

  class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
      pass

    def do_GET(self):
      assert self.path == "/api/agent/app-tools/"
      self._reply({"tools": [{"name": "test_echo", "description": "test",
                              "inputSchema": {"type": "object"}}]})

    def do_POST(self):
      assert self.path == "/api/agent/app-tools/call"
      payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
      if payload["arguments"].get("slow"):
        slow_started.set()
        assert fast_finished.wait(5), "short request was blocked behind slow request"
        # Keep the long reply behind the short one without relying on process
        # start order or a slow CI machine meeting a tiny timing threshold.
        time.sleep(0.05)
      else:
        assert slow_started.wait(5)
      self._reply({"result": self.headers["Authorization"], "is_error": False})
      if not payload["arguments"].get("slow"):
        fast_finished.set()

    def _reply(self, payload):
      data = json.dumps(payload).encode()
      self.send_response(200)
      self.send_header("Content-Type", "application/json")
      self.send_header("Content-Length", str(len(data)))
      self.end_headers()
      self.wfile.write(data)

  server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  try:
    monkeypatch.setenv("API_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("AGENT_TOKEN", "parent")
    monkeypatch.setenv("MOBIUS_HELPER_HOST", "1")
    monkeypatch.delenv("MOBIUS_CALLER_ENV_FILE", raising=False)
    slow_env = tmp_path / "slow.env"
    fast_env = tmp_path / "fast.env"
    slow_env.write_text("export AGENT_TOKEN=slow-caller\n")
    fast_env.write_text("export AGENT_TOKEN=fast-caller\n")
    control = _control()
    responses = _serve(control, [
      _call(1, "test_echo", {"slow": True, "_mobius_caller_env_file": str(slow_env)}),
      _call(2, "test_echo", {"_mobius_caller_env_file": str(fast_env)}),
      {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ])
    assert responses[0]["id"] in (2, 3)
    assert responses[-1]["id"] == 1
    by_id = {item["id"]: item for item in responses}
    assert by_id[1]["result"]["content"][0]["text"] == "Bearer slow-caller"
    assert by_id[2]["result"]["content"][0]["text"] == "Bearer fast-caller"
    assert by_id[3]["result"] == {}
    assert os.environ["AGENT_TOKEN"] == "parent"
  finally:
    server.shutdown()
    server.server_close()
    thread.join()


def test_parse_errors_and_worker_failures_keep_request_ids(monkeypatch):
  control = _control()
  output = io.StringIO()
  control.serve(io.StringIO('{bad\n{"jsonrpc":"2.0","id":"ok","method":"ping"}\n'), output)
  responses = [json.loads(line) for line in output.getvalue().splitlines()]
  assert any(item["error"]["code"] == -32700 for item in responses if "error" in item)
  assert next(item for item in responses if item["id"] == "ok")["result"] == {}

  class BadWorker:
    returncode = 0
    stdout = "not json"

  monkeypatch.setattr(control.subprocess, "run", lambda *_args, **_kwargs: BadWorker())
  response = control._dispatch_in_child({"id": "failed"})
  assert response["id"] == "failed"
  assert "outcome is unknown" in response["error"]["message"]

  BadWorker.returncode = 1
  BadWorker.stdout = ""
  crashed = control._dispatch_in_child({"id": 42})
  assert crashed["id"] == 42
  assert "outcome is unknown" in crashed["error"]["message"]

  BadWorker.returncode = 0
  BadWorker.stdout = '{"jsonrpc":"2.0","id":"other","result":{}}'
  mismatched = control._dispatch_in_child({"id": "failed"})
  assert mismatched["id"] == "failed"
  assert "outcome is unknown" in mismatched["error"]["message"]
  BadWorker.stdout = "null"
  missing = control._dispatch_in_child({"id": "failed"})
  assert missing["id"] == "failed"
  assert "outcome is unknown" in missing["error"]["message"]

  def timed_out(*_args, **_kwargs):
    raise subprocess.TimeoutExpired("worker", 620)

  monkeypatch.setattr(control.subprocess, "run", timed_out)
  timeout = control._dispatch_in_child({"id": "failed"})
  assert timeout["id"] == "failed"
  assert "outcome is unknown" in timeout["error"]["message"]


def test_notifications_emit_no_line_and_eof_drains_accepted_requests():
  control = _control()
  responses = _serve(control, [
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}},
    {"jsonrpc": "2.0", "id": "last", "method": "ping"},
  ])
  assert responses == [{"jsonrpc": "2.0", "id": "last", "result": {}}]


def test_saturation_refuses_unstarted_work_but_ping_still_responds(monkeypatch):
  control = _control()
  release = threading.Event()
  started = []

  def blocked(message):
    started.append(message["id"])
    assert release.wait(3)
    return control._response(message["id"], {})

  monkeypatch.setattr(control, "_dispatch_in_child", blocked)
  requests = [
    {"jsonrpc": "2.0", "id": n, "method": "tools/list"}
    for n in range(control.MAX_IN_FLIGHT_REQUESTS + 1)
  ] + [{"jsonrpc": "2.0", "id": "ping", "method": "ping"}]
  output = io.StringIO()
  thread = threading.Thread(target=control.serve, args=(
    io.StringIO("".join(json.dumps(item) + "\n" for item in requests)), output,
  ))
  thread.start()
  try:
    deadline = time.monotonic() + 2
    while output.getvalue().count("\n") < 2 and time.monotonic() < deadline:
      time.sleep(0.01)
    early = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {item["id"] for item in early} == {control.MAX_IN_FLIGHT_REQUESTS, "ping"}
    assert early[0]["error"]["message"] == "Server busy; request was not executed."
  finally:
    release.set()
    thread.join(timeout=4)
  assert not thread.is_alive()
  assert sorted(started) == list(range(control.MAX_IN_FLIGHT_REQUESTS))


def test_unexpected_future_exception_becomes_correlated_internal_error(monkeypatch):
  control = _control()

  def broken(_message):
    raise RuntimeError("private details must not escape")

  monkeypatch.setattr(control, "_dispatch_in_child", broken)
  response = _serve(control, [{"jsonrpc": "2.0", "id": "x", "method": "tools/list"}])
  assert response == [control._error("x", -32603, "Internal error")]


def test_worker_exception_keeps_request_id_and_hides_exception_text(monkeypatch):
  control = _control()

  def broken(_message):
    raise RuntimeError("private details")

  monkeypatch.setattr(control, "_dispatch_message", broken)
  monkeypatch.setattr(sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":17,"method":"ping"}'))
  output = io.StringIO()
  with monkeypatch.context() as patch:
    patch.setattr(sys, "stdout", output)
    assert control._dispatch_message_cli() == 0
  assert json.loads(output.getvalue()) == control._error(17, -32603, "Internal error")
