"""Preloaded app-service processes: run module setup once, fork per request.

A json-v1 request normally starts a fresh interpreter from the accepted app
revision (``app_services._run_spawned``). For a FastAPI service most of the
~0.9 s that costs is importing code, and an app's private requests wait for
each other in one serialized lane, so every queued request adds that second.

An entry that declares ``MOBIUS_PRELOAD = True`` promises that its module-level
setup is request independent: it reads no per-request value (``APP_TOKEN``, the
request) and starts no threads. For such an entry the platform keeps one host
process per (app, runtime revision), ``service_preload_host.py``, which runs the
setup once and forks a child per request. The child receives the request's own
environment and stdio, runs only the entry's ``if __name__ == "__main__":``
block, and exits: each request still gets a fresh process, its own token, a
killable process group, bounded output, and the entry's own error handling.

Preloading is an optimization, never a dependency. A request is served by an
ordinary spawn whenever no ready host exists (the first request after start or
Apply, which also starts the host in the background), the entry has not opted
in, preloading failed, or the host died before forking the request's child.

Lifecycle: a host pins its app's runtime files like any reader, is retired
before Apply/install prunes older runtime trees, after ``IDLE_SECONDS`` without
requests, and when its revision is no longer current. A host that exits on
its own is dropped and not restarted for RETRY_AFTER_FAILURE_SECONDS. Its
control socket's other end lives only in this backend process, so a host exits
by itself when the backend stops or crashes. The host holds no credential; each
child gets a freshly minted token with its request.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from app import service_preload_host
from app.applied_app_runtime import hold_runtime
from app.config import get_settings

log = logging.getLogger(__name__)

IDLE_SECONDS = 600
READY_TIMEOUT_SECONDS = 30
# A live host forks within milliseconds; one that cannot is replaced.
FORK_TIMEOUT_SECONDS = 5
RETRY_AFTER_FAILURE_SECONDS = 300
_STATUS = service_preload_host.STATUS
# Frozen at backend start: a host started later runs the protocol this process
# speaks even if the file on disk changes before the next restart.
_HOST_SOURCE = Path(service_preload_host.__file__).read_bytes()


class PreloadUnavailable(Exception):
  """The request never reached a preloaded child; serve it with a spawn."""


@dataclass(eq=False)
class _Host:
  key: tuple[int, str]
  slug: str
  process: asyncio.subprocess.Process
  control: socket.socket
  pin: object
  in_flight: int = 0
  closed: bool = False
  idle: asyncio.TimerHandle | None = None
  diagnostics: asyncio.Task | None = field(default=None, repr=False)

  def alive(self) -> bool:
    return not self.closed and self.process.returncode is None

  def mark_failed(self) -> None:
    _failed_at[self.key] = time.monotonic()
    _close(self)


_hosts: dict[tuple[int, str], _Host] = {}
_starting: dict[tuple[int, str], asyncio.Task] = {}
_failed_at: dict[tuple[int, str], float] = {}
_eligible: dict[tuple[int, str], bool] = {}


def _key(app) -> tuple[int, str] | None:
  revision = getattr(app, "runtime_revision", None)
  if not isinstance(revision, str) or not revision:
    return None
  return int(app.id), revision


def _declares_preload(entry: Path) -> bool:
  try:
    source = entry.read_bytes()
  except OSError:
    return False
  return service_preload_host.preloadable_tree(source, str(entry)) is not None


def _host_program() -> Path:
  digest = hashlib.sha256(_HOST_SOURCE).hexdigest()[:16]
  parent = Path(get_settings().data_dir) / "run" / "service-preload"
  parent.mkdir(parents=True, exist_ok=True)
  program = parent / f"host-{digest}.py"
  if not program.is_file() or program.read_bytes() != _HOST_SOURCE:
    staged = parent / f".host-{digest}.{os.getpid()}.tmp"
    staged.write_bytes(_HOST_SOURCE)
    staged.replace(program)
  return program


def ready_host(app, python: str, entry: Path, environment: dict[str, str]) -> _Host | None:
  """Return a ready host for the app's current revision, if one exists.

  When the entry opts in and no host exists, start one in the background and
  return None, so this request uses an ordinary spawn instead of waiting.
  ``python`` is the interpreter the revision runs with (``app_services``); it
  is fixed per revision, so the host key need not include it.
  """
  key = _key(app)
  if key is None:
    return None
  host = _hosts.get(key)
  if host is not None:
    if host.alive():
      return host
    _close(host)
  if key in _starting:
    return None
  failed_at = _failed_at.get(key)
  if failed_at is not None and time.monotonic() - failed_at < RETRY_AFTER_FAILURE_SECONDS:
    return None
  if key not in _eligible:
    _eligible[key] = _declares_preload(entry)
  if not _eligible[key]:
    return None
  retire(key[0], keep_revision=key[1])
  task = asyncio.get_running_loop().create_task(
    _start_logged(key, str(getattr(app, "slug", key[0])), python, entry, environment),
  )
  _starting[key] = task
  task.add_done_callback(lambda _task: _starting.pop(key, None))
  return None


async def _start_logged(key, slug, python, entry, environment) -> None:
  try:
    await start(key, slug, python, entry, environment)
  except Exception:
    log.warning("Could not preload the %s service", slug, exc_info=True)
    _failed_at[key] = time.monotonic()


async def start(
  key: tuple[int, str], slug: str, python: str, entry: Path,
  environment: dict[str, str],
) -> _Host | None:
  """Start and register a host; return None when the entry cannot preload."""
  parent_end, host_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
  pin = hold_runtime(key[0])
  process = None
  diagnostics = None
  loop = asyncio.get_running_loop()
  try:
    env = {name: value for name, value in environment.items() if name != "APP_TOKEN"}
    env["MOBIUS_PRELOAD_CONTROL_FD"] = str(host_end.fileno())
    env["MOBIUS_PRELOAD_ENTRY"] = str(entry)
    process = await asyncio.create_subprocess_exec(
      python, str(_host_program()),
      stdin=asyncio.subprocess.DEVNULL,
      stdout=asyncio.subprocess.DEVNULL,
      stderr=asyncio.subprocess.PIPE,
      cwd=str(entry.parent),
      env=env,
      start_new_session=True,
      pass_fds=(host_end.fileno(),),
    )
    host_end.close()
    parent_end.setblocking(False)
    # Drain setup output from the start: a chatty setup must not block on a
    # full pipe, and a failed one should still leave its diagnostics.
    diagnostics = loop.create_task(_log_host_output(slug, process))
    signal_byte = await asyncio.wait_for(
      loop.sock_recv(parent_end, 1), timeout=READY_TIMEOUT_SECONDS,
    )
  except BaseException:
    host_end.close()
    parent_end.close()
    pin.close()
    if process is not None:
      _kill_group(process.pid)
      with contextlib.suppress(Exception):
        await process.wait()
    if diagnostics is not None:
      with contextlib.suppress(BaseException):
        await diagnostics
    raise
  host = _Host(
    key=key, slug=slug, process=process, control=parent_end, pin=pin,
    diagnostics=diagnostics,
  )
  if signal_byte != b"R":
    host.mark_failed()
    await diagnostics
    return None
  _hosts[key] = host
  _failed_at.pop(key, None)
  _schedule_idle(host)
  # A registered host that exits by itself is dropped with a backoff instead
  # of being restarted by every following request.
  diagnostics.add_done_callback(
    lambda _task: host.mark_failed() if _hosts.get(host.key) is host else None,
  )
  return host


async def _log_host_output(slug: str, process: asyncio.subprocess.Process) -> None:
  """Drain the host's own stderr (preload diagnostics) until it exits."""
  assert process.stderr is not None
  with contextlib.suppress(Exception):
    while chunk := await process.stderr.read(4096):
      text = chunk.decode("utf-8", errors="replace").strip()
      if text:
        log.warning("App service %s preload host: %s", slug, text[-1000:])
  with contextlib.suppress(Exception):
    await process.wait()


def _kill_group(pid: int | None) -> None:
  if not pid:
    return
  with contextlib.suppress(ProcessLookupError, PermissionError):
    os.killpg(pid, signal.SIGKILL)


def _schedule_idle(host: _Host) -> None:
  if host.idle is not None:
    host.idle.cancel()
  host.idle = asyncio.get_running_loop().call_later(IDLE_SECONDS, _retire_if_idle, host)


def _retire_if_idle(host: _Host) -> None:
  if host.in_flight:
    _schedule_idle(host)
  elif _hosts.get(host.key) is host:
    _close(host)


def _close(host: _Host) -> None:
  if host.closed:
    return
  host.closed = True
  if _hosts.get(host.key) is host:
    del _hosts[host.key]
  if host.idle is not None:
    host.idle.cancel()
    host.idle = None
  host.control.close()
  # The host leads its own process group; request children are separate
  # sessions and finish under their own request's pin and deadline. A host
  # that has already been reaped may have had its pid reused.
  if host.process.returncode is None:
    _kill_group(host.process.pid)
  host.pin.close()


def retire(app_id: int, *, keep_revision: str | None = None) -> None:
  """Retire an app's hosts, e.g. before its older runtime trees are pruned."""
  for key, task in list(_starting.items()):
    if key[0] == int(app_id) and key[1] != keep_revision:
      task.cancel()
  for key, host in list(_hosts.items()):
    if key[0] == int(app_id) and key[1] != keep_revision:
      _close(host)
  for key in [key for key in _eligible if key[0] == int(app_id) and key[1] != keep_revision]:
    _eligible.pop(key, None)
    _failed_at.pop(key, None)


async def shutdown() -> None:
  """Stop every host and wait for it. Hosts also exit when the backend does."""
  starting = list(_starting.values())
  for task in starting:
    task.cancel()
  await asyncio.gather(*starting, return_exceptions=True)
  hosts = list(_hosts.values())
  for host in hosts:
    _close(host)
  await asyncio.gather(
    *(host.diagnostics for host in hosts if host.diagnostics is not None),
    return_exceptions=True,
  )


async def read_bounded(reader: asyncio.StreamReader, limit: int) -> bytes:
  chunks: list[bytes] = []
  total = 0
  while True:
    chunk = await reader.read(64 * 1024)
    if not chunk:
      return b"".join(chunks)
    total += len(chunk)
    if total > limit:
      raise ValueError("output limit exceeded")
    chunks.append(chunk)


async def run(
  host: _Host, environment: dict[str, str], request: bytes, *,
  timeout_seconds: float, max_stdout: int, max_stderr: int,
) -> tuple[bytes, bytes, int]:
  """Serve one request in a fresh child of ``host``.

  Returns (stdout, stderr, exit code) like a spawned process. Raises
  TimeoutError or ValueError after killing the child's process group when the
  request exceeds its deadline or output limits, OSError when the child stops
  accepting its request, and PreloadUnavailable when no child started (the
  caller then spawns instead).
  """
  pairs = [socket.socketpair() for _ in range(3)]
  stdio, stderr, status = (parent for parent, _child in pairs)
  # A socket belongs to its stream once wrapped; close the rest directly.
  unwrapped = {stdio, stderr, status}
  writers: list[asyncio.StreamWriter] = []
  tasks: list[asyncio.Task] = []

  async def wrap(sock: socket.socket):
    reader, writer = await asyncio.open_connection(sock=sock)
    unwrapped.discard(sock)
    writers.append(writer)
    return reader, writer

  loop = asyncio.get_running_loop()
  deadline = loop.time() + timeout_seconds
  host.in_flight += 1
  pid = None
  try:
    try:
      socket.send_fds(
        host.control, [json.dumps(environment).encode()],
        [child.fileno() for _parent, child in pairs],
      )
    except BlockingIOError as exc:
      raise PreloadUnavailable("the preload host is busy") from exc
    except OSError as exc:
      _close(host)
      raise PreloadUnavailable(str(exc)) from exc
    finally:
      for _parent, child in pairs:
        child.close()
    status_reader, _status_writer = await wrap(status)
    try:
      async with asyncio.timeout(FORK_TIMEOUT_SECONDS):
        pid = _STATUS.unpack(await status_reader.readexactly(_STATUS.size))[0]
    except (asyncio.IncompleteReadError, TimeoutError) as exc:
      # No child started, so spawning the request cannot run it twice.
      host.mark_failed()
      raise PreloadUnavailable("the preload host did not start the request") from exc
    async with asyncio.timeout_at(deadline):
      stdio_reader, stdio_writer = await wrap(stdio)
      stderr_reader, _stderr_writer = await wrap(stderr)

      async def send_request() -> None:
        stdio_writer.write(request)
        await stdio_writer.drain()
        stdio_writer.write_eof()

      async def exit_code() -> int:
        try:
          return _STATUS.unpack(await status_reader.readexactly(_STATUS.size))[0]
        except asyncio.IncompleteReadError:
          return -1

      tasks = [
        asyncio.ensure_future(send_request()),
        asyncio.ensure_future(read_bounded(stdio_reader, max_stdout)),
        asyncio.ensure_future(read_bounded(stderr_reader, max_stderr)),
        asyncio.ensure_future(exit_code()),
      ]
      _sent, stdout, errors, code = await asyncio.gather(*tasks)
      return stdout, errors, code
  finally:
    # As with a spawn, nothing the request started outlives its reply. The
    # synchronous cleanup runs first, so a second cancellation during the
    # final await cannot leak descriptors or the in-flight count.
    _kill_group(pid)
    for task in tasks:
      task.cancel()
    for writer in writers:
      writer.close()
    for sock in unwrapped:
      sock.close()
    host.in_flight -= 1
    if _hosts.get(host.key) is host:
      _schedule_idle(host)
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
