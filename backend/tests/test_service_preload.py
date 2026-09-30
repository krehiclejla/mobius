"""Preloaded app services keep the spawn contract while skipping repeat imports."""

import asyncio
import json
import os
import signal
import sys
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app import app_services, service_preload
from app.service_preload_host import preloadable_tree

ENTRY = '''
import json, os, sys, time
MOBIUS_PRELOAD = True
SETUP_PID = os.getpid()
SETUP_TOKEN = os.environ.get("APP_TOKEN")
with open(os.environ["PRELOAD_LOG"], "a") as log:
  log.write("setup\\n")
STATE = {"touched": False}

if __name__ == "__main__":
  request = json.loads(sys.stdin.read() or "{}")
  mode = request.get("mode")
  if mode == "exit":
    raise SystemExit(request["code"])
  if mode == "crash":
    raise RuntimeError("boom")
  if mode == "subprocess":
    import subprocess
    print(json.dumps({"status": 200, "body": {"child": subprocess.run(["sh", "-c", "exit 7"]).returncode}}))
    raise SystemExit(0)
  if mode == "handoff":
    import atexit, threading
    def late(name):
      time.sleep(0.2)
      open(os.path.join(request["dir"], name), "w").write("done")
    threading.Thread(target=late, args=("thread",)).start()
    atexit.register(lambda: open(os.path.join(request["dir"], "atexit"), "w").write("done"))
    print(json.dumps({"status": 200, "body": {}}))
    raise SystemExit(0)
  if mode == "loud":
    sys.stdout.write("x" * 200000)
    raise SystemExit(0)
  if mode == "fds":
    print(json.dumps({"status": 200, "body": {"fds": sorted(int(fd) for fd in os.listdir("/proc/self/fd"))}}))
    raise SystemExit(0)
  if mode == "hang":
    import subprocess
    worker = subprocess.Popen(["sleep", "30"])
    with open(request["pid_file"], "w") as handle:
      handle.write(str(worker.pid))
    time.sleep(30)
  touched_before = STATE["touched"]
  STATE["touched"] = True
  print(json.dumps({"status": 200, "body": {
    "token": os.environ.get("APP_TOKEN"),
    "setup_token": SETUP_TOKEN,
    "pid": os.getpid(),
    "parent": os.getppid(),
    "touched_before": touched_before,
    "cwd": os.getcwd(),
  }}))
'''


@pytest_asyncio.fixture(autouse=True)
async def fresh_preload_state():
  yield
  await service_preload.shutdown()
  service_preload._failed_at.clear()
  service_preload._eligible.clear()


def _entry(tmp_path, source=ENTRY):
  root = tmp_path / "runtime"
  root.mkdir()
  entry = root / "service.py"
  entry.write_text(source)
  return entry


def _environment(tmp_path, **extra):
  return {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "PRELOAD_LOG": str(tmp_path / "setup.log"),
    **extra,
  }


async def _request(host, tmp_path, token="token", **request):
  return await service_preload.run(
    host, _environment(tmp_path, APP_TOKEN=token), json.dumps(request).encode(),
    timeout_seconds=10, max_stdout=1 << 20, max_stderr=1 << 16,
  )


def _gone(pid: int) -> bool:
  try:
    with open(f"/proc/{pid}/stat") as handle:
      return handle.read().split(")")[-1].split()[0] == "Z"
  except FileNotFoundError:
    return True


@pytest.mark.asyncio
async def test_only_entries_that_declare_preload_and_end_in_their_main_block_qualify():
  assert preloadable_tree(ENTRY.encode(), "service.py") is not None
  assert preloadable_tree(ENTRY.replace("MOBIUS_PRELOAD = True", "").encode(), "s.py") is None
  assert preloadable_tree(ENTRY.replace("MOBIUS_PRELOAD = True", "MOBIUS_PRELOAD = 1").encode(), "s.py") is None
  assert preloadable_tree((ENTRY + "\nprint('after main')\n").encode(), "s.py") is None
  assert preloadable_tree(b"def broken(:\n", "s.py") is None


@pytest.mark.asyncio
async def test_setup_runs_once_and_each_request_gets_a_fresh_process_and_its_own_token(tmp_path):
  entry = _entry(tmp_path)
  host = await service_preload.start(
    (901, "rev-a"), "demo", sys.executable, entry, _environment(tmp_path, APP_TOKEN="never-in-the-host"),
  )
  assert host is not None
  replies = []
  for token in ("token-1", "token-2"):
    stdout, stderr, code = await _request(host, tmp_path, token)
    assert code == 0, stderr
    replies.append(json.loads(stdout)["body"])
  assert [reply["token"] for reply in replies] == ["token-1", "token-2"]
  # The long-lived host never holds a credential.
  assert {reply["setup_token"] for reply in replies} == {None}
  assert replies[0]["pid"] != replies[1]["pid"]
  assert {reply["parent"] for reply in replies} == {host.process.pid}
  # A request's in-memory changes never reach the next request.
  assert [reply["touched_before"] for reply in replies] == [False, False]
  assert {reply["cwd"] for reply in replies} == {str(entry.parent)}
  assert (tmp_path / "setup.log").read_text() == "setup\n"


@pytest.mark.asyncio
async def test_exit_codes_and_diagnostics_match_a_spawned_entry(tmp_path):
  host = await service_preload.start((902, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  assert (await _request(host, tmp_path, mode="exit", code=3))[2] == 3
  _stdout, stderr, code = await _request(host, tmp_path, mode="crash")
  assert code == 1
  assert b"RuntimeError: boom" in stderr
  _stdout, stderr, code = await _request(host, tmp_path, mode="exit", code="stopped")
  assert (code, stderr.strip()) == (1, b"stopped")
  stdout, _stderr, code = await _request(host, tmp_path)
  assert code == 0 and json.loads(stdout)["status"] == 200


@pytest.mark.asyncio
async def test_a_request_past_its_deadline_is_killed_with_everything_it_started(tmp_path):
  host = await service_preload.start((903, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  pid_file = tmp_path / "worker.pid"
  with pytest.raises(TimeoutError):
    await service_preload.run(
      host, _environment(tmp_path), json.dumps({"mode": "hang", "pid_file": str(pid_file)}).encode(),
      timeout_seconds=3, max_stdout=1 << 20, max_stderr=1 << 16,
    )
  worker = int(pid_file.read_text())
  deadline = time.monotonic() + 5
  while not _gone(worker) and time.monotonic() < deadline:
    await asyncio.sleep(0.05)
  assert _gone(worker)
  # The host itself keeps serving.
  assert (await _request(host, tmp_path))[2] == 0


@pytest.mark.asyncio
async def test_child_processes_report_their_real_exit_status(tmp_path):
  # The host ignores SIGCHLD; each request child must restore it or
  # subprocess results would read as 0.
  host = await service_preload.start((909, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  stdout, stderr, code = await _request(host, tmp_path, mode="subprocess")
  assert code == 0, stderr
  assert json.loads(stdout)["body"]["child"] == 7


@pytest.mark.asyncio
async def test_a_request_finishes_its_threads_and_atexit_work_like_a_spawn(tmp_path):
  host = await service_preload.start((910, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  _stdout, stderr, code = await _request(host, tmp_path, mode="handoff", dir=str(tmp_path))
  assert code == 0, stderr
  assert (tmp_path / "thread").read_text() == "done"
  assert (tmp_path / "atexit").read_text() == "done"


@pytest.mark.asyncio
async def test_output_beyond_its_limit_ends_the_request(tmp_path):
  host = await service_preload.start((911, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  with pytest.raises(ValueError):
    await service_preload.run(
      host, _environment(tmp_path), json.dumps({"mode": "loud"}).encode(),
      timeout_seconds=10, max_stdout=1024, max_stderr=1024,
    )
  assert (await _request(host, tmp_path))[2] == 0


@pytest.mark.asyncio
async def test_concurrent_requests_each_run_in_their_own_process(tmp_path):
  host = await service_preload.start((912, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  replies = await asyncio.gather(*(_request(host, tmp_path, token=f"t{n}") for n in range(6)))
  bodies = [json.loads(stdout)["body"] for stdout, _stderr, _code in replies]
  assert sorted(body["token"] for body in bodies) == sorted(f"t{n}" for n in range(6))
  assert len({body["pid"] for body in bodies}) == 6
  assert host.in_flight == 0


@pytest.mark.asyncio
async def test_a_request_child_holds_only_its_own_descriptors(tmp_path):
  host = await service_preload.start((913, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  stdout, stderr, code = await _request(host, tmp_path, mode="fds")
  assert code == 0, stderr
  # stdin/stdout, stderr, the status socket, and the directory being listed.
  assert len(json.loads(stdout)["body"]["fds"]) <= 5


@pytest.mark.asyncio
async def test_undeclared_entries_are_spawned_and_never_start_a_host(tmp_path):
  entry = _entry(tmp_path, ENTRY.replace("MOBIUS_PRELOAD = True\n", ""))
  app = SimpleNamespace(id=904, slug="demo", runtime_revision="rev-a")
  assert service_preload.ready_host(app, sys.executable, entry, _environment(tmp_path)) is None
  assert not service_preload._starting
  assert not service_preload._hosts


@pytest.mark.asyncio
async def test_a_failed_preload_falls_back_and_is_not_retried_at_once(tmp_path):
  entry = _entry(tmp_path, ENTRY.replace("STATE = {", "raise ImportError('missing dependency')\nSTATE = {"))
  assert await service_preload.start((905, "rev-a"), "demo", sys.executable, entry, _environment(tmp_path)) is None
  app = SimpleNamespace(id=905, slug="demo", runtime_revision="rev-a")
  assert service_preload.ready_host(app, sys.executable, entry, _environment(tmp_path)) is None
  assert not service_preload._starting


@pytest.mark.asyncio
async def test_a_host_exits_by_itself_when_the_backend_side_goes_away(tmp_path):
  host = await service_preload.start((908, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  # Closing the backend's end is what a stopped or crashed backend does.
  host.control.close()
  assert await asyncio.wait_for(host.process.wait(), timeout=5) == 0


@pytest.mark.asyncio
async def test_a_dead_host_hands_its_request_back_to_the_spawn_path(tmp_path, monkeypatch):
  released = []
  monkeypatch.setattr(service_preload, "hold_runtime", lambda _app_id: SimpleNamespace(
    close=lambda: released.append(True),
  ))
  host = await service_preload.start((906, "rev-a"), "demo", sys.executable, _entry(tmp_path), _environment(tmp_path))
  os.killpg(host.process.pid, signal.SIGKILL)
  await host.process.wait()
  with pytest.raises(service_preload.PreloadUnavailable):
    await _request(host, tmp_path)
  assert (906, "rev-a") not in service_preload._hosts
  assert released == [True]


@pytest.mark.asyncio
async def test_invoke_service_switches_to_the_preloaded_host_once_it_is_ready(tmp_path, monkeypatch):
  entry = _entry(tmp_path)
  app = SimpleNamespace(id=907, slug="demo", runtime_revision="rev-a")
  monkeypatch.setattr(app_services, "service_contract", lambda *a, **k: {"entry": "service.py"})
  monkeypatch.setattr(app_services, "service_entry", lambda *_a: entry)
  monkeypatch.setattr(app_services, "service_environment", lambda *_a, **_k: _environment(
    tmp_path, APP_TOKEN="fresh",
  ))
  envelope = {"schema": 1, "method": "GET", "path": "/", "query": {}, "headers": {}, "body": None}

  status, spawned, _headers, _media = await app_services.invoke_service(app, None, envelope)
  assert status == 200 and spawned["parent"] == os.getpid()
  await asyncio.gather(*service_preload._starting.values())
  host = service_preload._hosts[(907, "rev-a")]

  status, preloaded, _headers, _media = await app_services.invoke_service(app, None, envelope)
  assert status == 200 and preloaded["parent"] == host.process.pid
  assert preloaded["token"] == spawned["token"] == "fresh"

  # A new accepted revision retires the old host instead of serving from it.
  app.runtime_revision = "rev-b"
  status, _body, _headers, _media = await app_services.invoke_service(app, None, envelope)
  assert status == 200
  assert (907, "rev-a") not in service_preload._hosts
  await asyncio.gather(*service_preload._starting.values())
  assert (907, "rev-b") in service_preload._hosts


def test_the_production_event_loop_starts_hosts_with_only_their_own_descriptors(tmp_path):
  # The backend runs on uvloop, which ignores close_fds when spawning; the
  # host must still hand each request nothing but its own descriptors.
  uvloop = pytest.importorskip("uvloop")
  entry = _entry(tmp_path)

  async def scenario():
    inherited = open(os.devnull)  # a descriptor a careless spawn would leak
    try:
      host = await service_preload.start((914, "rev-a"), "demo", sys.executable, entry, _environment(tmp_path))
      stdout, stderr, code = await _request(host, tmp_path, mode="fds")
      assert code == 0, stderr
      assert len(json.loads(stdout)["body"]["fds"]) <= 5
    finally:
      inherited.close()
      await service_preload.shutdown()

  with asyncio.Runner(loop_factory=uvloop.new_event_loop) as runner:
    runner.run(scenario())
