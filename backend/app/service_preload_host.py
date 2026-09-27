"""Standalone host for one preloaded json-v1 app service entry.

``app.service_preload`` starts this file as its own process for one
(app, accepted runtime revision). It imports nothing from the platform: it runs
the entry's module-level setup once, then forks one child per request. Each
child becomes its own session, takes the request's environment and stdio from
descriptors the platform passed over the control socket, runs only the entry's
``if __name__ == "__main__":`` block, and exits. Requests therefore keep the
spawn contract (a fresh process, its own token, a killable process group,
bounded stdio) without paying the import cost again.

Wire contract with ``app.service_preload`` (a SOCK_SEQPACKET control socket):

- host -> platform, once: ``b"R"`` when ready, ``b"F"`` when preloading failed.
- platform -> host, per request: the request environment as a JSON object with
  three descriptors attached: stdio socket, stderr socket, status socket.
- child -> platform on the status socket: its pid, then its exit code, each a
  4-byte big-endian signed integer.
"""

from __future__ import annotations

import __future__
import ast
import atexit
import gc
import json
import os
import signal
import socket
import struct
import sys
import threading
import traceback
import types

PRELOAD_DECLARATION = "MOBIUS_PRELOAD"
STATUS = struct.Struct("!i")


def _is_main_guard(node) -> bool:
  return (
    isinstance(node, ast.If)
    and not node.orelse
    and isinstance(node.test, ast.Compare)
    and isinstance(node.test.left, ast.Name)
    and node.test.left.id == "__name__"
    and len(node.test.ops) == 1
    and isinstance(node.test.ops[0], ast.Eq)
    and len(node.test.comparators) == 1
    and isinstance(node.test.comparators[0], ast.Constant)
    and node.test.comparators[0].value == "__main__"
  )


def _declares_preload(tree: ast.Module) -> bool:
  for node in tree.body:
    if (
      isinstance(node, ast.Assign)
      and len(node.targets) == 1
      and isinstance(node.targets[0], ast.Name)
      and node.targets[0].id == PRELOAD_DECLARATION
      and isinstance(node.value, ast.Constant)
      and node.value.value is True
    ):
      return True
  return False


def preloadable_tree(source: bytes, filename: str) -> ast.Module | None:
  """Return the entry's syntax tree when it opts into preloading.

  An entry opts in with a top-level ``MOBIUS_PRELOAD = True`` and must end with
  its ``if __name__ == "__main__":`` block, which is the per-request part.
  """
  try:
    tree = ast.parse(source, filename)
  except (SyntaxError, ValueError):
    return None
  if not tree.body or not _is_main_guard(tree.body[-1]) or not _declares_preload(tree):
    return None
  return tree


def _future_flags(tree: ast.Module) -> int:
  flags = 0
  for node in tree.body:
    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
      for alias in node.names:
        feature = getattr(__future__, alias.name, None)
        if feature is not None:
          flags |= feature.compiler_flag
  return flags


def _preload(entry: str):
  with open(entry, "rb") as handle:
    source = handle.read()
  tree = preloadable_tree(source, entry)
  if tree is None:
    raise RuntimeError("the entry does not declare MOBIUS_PRELOAD = True")
  guard = tree.body[-1]
  setup = compile(
    ast.Module(body=tree.body[:-1], type_ignores=[]), entry, "exec", dont_inherit=True,
  )
  per_request = compile(
    ast.Module(body=guard.body, type_ignores=[]), entry, "exec",
    flags=_future_flags(tree), dont_inherit=True,
  )
  module = types.ModuleType("__main__")
  module.__file__ = entry
  sys.modules["__main__"] = module
  exec(setup, module.__dict__)
  return per_request, module.__dict__


def _exit_code(value) -> int:
  # Mirror the interpreter's own SystemExit handling.
  if value is None:
    return 0
  if isinstance(value, int):
    return value
  print(value, file=sys.stderr)
  return 1


def _run_request(per_request, namespace) -> int:
  try:
    exec(per_request, namespace)
  except SystemExit as exc:
    code = _exit_code(exc.code)
  except BaseException:
    traceback.print_exc()
    code = 1
  else:
    code = 0
  # What the interpreter does between the main module finishing and exit:
  # wait for non-daemon threads (including executor workers), then run
  # atexit handlers, so work a request handed off is not cut short.
  try:
    threading._shutdown()
  except BaseException:
    traceback.print_exc()
  atexit._run_exitfuncs()
  return code


def _serve_child(per_request, namespace, environment: dict, fds: list[int]) -> None:
  """Run one request in this forked child and exit; never returns."""
  code = 1
  status = None
  try:
    os.setsid()
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    stdio, stderr, status_fd = fds
    os.set_inheritable(status_fd, False)
    status = socket.socket(fileno=status_fd)
    status.sendall(STATUS.pack(os.getpid()))
    os.dup2(stdio, 0)
    os.dup2(stdio, 1)
    os.dup2(stderr, 2)
    os.close(stdio)
    os.close(stderr)
    os.environ.clear()
    os.environ.update({str(key): str(value) for key, value in environment.items()})
    code = _run_request(per_request, namespace)
  except BaseException:
    traceback.print_exc()
    code = 1
  finally:
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
      try:
        stream.flush()
      except BaseException:
        code = code or 1
    # Report exactly what a spawned process's exit status would carry.
    code &= 0xFF
    if status is not None:
      try:
        status.sendall(STATUS.pack(code))
      except BaseException:
        pass
    os._exit(code)


def main() -> int:
  control_fd = int(os.environ.pop("MOBIUS_PRELOAD_CONTROL_FD"))
  # Some event loops ignore close_fds when spawning; start from stdio and the
  # control socket only, so no request inherits the backend's descriptors.
  os.closerange(3, control_fd)
  os.closerange(control_fd + 1, os.sysconf("SC_OPEN_MAX"))
  control = socket.socket(fileno=control_fd)
  entry = os.environ.pop("MOBIUS_PRELOAD_ENTRY")
  root = os.path.dirname(entry)
  sys.path[0] = root
  sys.argv = [entry]
  try:
    per_request, namespace = _preload(entry)
    # fork() copies only the calling thread; setup that started threads
    # cannot be duplicated safely.
    if threading.active_count() != 1:
      raise RuntimeError("module setup started threads, so it cannot be forked safely")
  except BaseException:
    traceback.print_exc()
    control.sendall(b"F")
    return 1
  sys.stdout.flush()
  sys.stderr.flush()
  # Keep the setup's objects out of collection so children do not dirty (and
  # copy) the pages they share with the host.
  gc.collect()
  gc.freeze()
  # The kernel reaps finished children; the platform reads each exit code
  # from that child's own status socket.
  signal.signal(signal.SIGCHLD, signal.SIG_IGN)
  control.sendall(b"R")
  while True:
    try:
      message, fds, _flags, _address = socket.recv_fds(control, 1 << 16, 3)
    except OSError:
      return 0
    if not message:
      return 0
    try:
      environment = json.loads(message)
    except ValueError:
      for fd in fds:
        os.close(fd)
      continue
    if len(fds) != 3 or not isinstance(environment, dict):
      for fd in fds:
        os.close(fd)
      continue
    if os.fork() == 0:
      control.close()
      _serve_child(per_request, namespace, environment, fds)
    for fd in fds:
      os.close(fd)


if __name__ == "__main__":
  raise SystemExit(main())
