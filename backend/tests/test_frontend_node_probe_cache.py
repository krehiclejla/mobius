"""Short shell validation probes must not leave the Node binary cached."""

import subprocess
from types import SimpleNamespace

import pytest

from app import frontend_watcher as fw


@pytest.mark.parametrize("probe", ["globals", "compile-cache"])
@pytest.mark.parametrize("outcome", ["success", "rejected", "timeout", "missing"])
def test_node_probe_cleanup_follows_exit_without_changing_outcome(
  tmp_path, monkeypatch, probe, outcome,
):
  checker = tmp_path / "check.mjs"
  checker.write_text("// test checker")
  monkeypatch.setattr(fw, "_BUILT_GLOBAL_CHECK", checker)
  events = []

  def run(argv, **kwargs):
    assert argv[0] == "node"
    events.append("exit")
    if outcome == "timeout":
      raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    if outcome == "missing":
      raise FileNotFoundError("Node missing")
    return SimpleNamespace(
      returncode=0 if outcome == "success" else 1,
      stdout=str(tmp_path / "compile-cache") if outcome == "success" else "undeclared global",
      stderr="failed",
    )

  monkeypatch.setattr(fw.subprocess, "run", run)
  def cleanup(env=None):
    assert env == ({} if probe == "compile-cache" else None)
    events.append("cleanup")

  monkeypatch.setattr(fw, "_reclaim_node_probe_cache", cleanup)
  if probe == "globals":
    if outcome == "success":
      assert fw._validate_built_globals(tmp_path) is None
    else:
      with pytest.raises(fw._BuiltGlobalValidationError):
        fw._validate_built_globals(tmp_path)
  else:
    assert fw._current_node_compile_cache_dir({}) == (
      tmp_path / "compile-cache" if outcome == "success" else None
    )
  assert events == ["exit", "cleanup"]


def test_node_cleanup_advises_only_resolved_executable_not_dependencies(tmp_path, monkeypatch):
  node = tmp_path / "actual-node"
  node.write_bytes(b"test executable")
  link = tmp_path / "node"
  link.symlink_to(node)
  monkeypatch.setattr(fw.shutil, "which", lambda command, **kw: str(link) if command == "node" else None)
  calls = []
  monkeypatch.setattr(fw, "reclaim_file_cache", lambda paths, **kw: calls.append((paths, kw)))
  fw._reclaim_node_probe_cache()
  assert calls == [((node,), {"skip_mapped": False})]


def test_missing_node_cleanup_does_no_work(monkeypatch):
  monkeypatch.setattr(fw.shutil, "which", lambda _command, **kw: None)
  monkeypatch.setattr(fw, "reclaim_file_cache", lambda *_a, **_k: pytest.fail("no file to advise"))
  fw._reclaim_node_probe_cache()


@pytest.mark.parametrize("rejected", [False, True])
def test_cache_cleanup_failure_never_masks_safety_verdict(tmp_path, monkeypatch, rejected):
  checker = tmp_path / "check.mjs"
  checker.write_text("// test checker")
  monkeypatch.setattr(fw, "_BUILT_GLOBAL_CHECK", checker)
  monkeypatch.setattr(fw.shutil, "which", lambda _command, **kw: "/test/node")
  monkeypatch.setattr(fw.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
    returncode=1 if rejected else 0, stdout="unsafe identifier", stderr="",
  ))

  def failed_cleanup(*_args, **_kwargs):
    raise RuntimeError("optional cleanup unavailable")

  monkeypatch.setattr(fw, "reclaim_file_cache", failed_cleanup)
  if rejected:
    with pytest.raises(fw._BuiltGlobalValidationError, match="unsafe identifier"):
      fw._validate_built_globals(tmp_path)
  else:
    assert fw._validate_built_globals(tmp_path) is None


def test_missing_checker_is_still_rejected_without_running_node(tmp_path, monkeypatch):
  monkeypatch.setattr(fw, "_BUILT_GLOBAL_CHECK", tmp_path / "absent.mjs")
  monkeypatch.setattr(fw.subprocess, "run", lambda *_a, **_k: pytest.fail("no checker"))
  with pytest.raises(fw._BuiltGlobalValidationError, match="missing"):
    fw._validate_built_globals(tmp_path)


@pytest.mark.parametrize("env", [{"PATH": "/child/node/bin"}, {}])
def test_node_cleanup_uses_child_search_path_not_parent_environment(monkeypatch, env):
  monkeypatch.setenv("PATH", "/parent/node/bin")
  calls = []
  monkeypatch.setattr(fw.shutil, "which", lambda command, **kw: calls.append((command, kw)))
  fw._reclaim_node_probe_cache(env)
  assert calls == [("node", {"path": env.get("PATH", fw.os.defpath)})]
