"""A merged contribution's local draft never meets its reviewed version as a conflict."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app import app_git, platform_update

_BASE = "def greet(name):\n  return 'hi ' + name\n"
_DRAFT = "def greet(name):\n  return 'hello ' + name\n"
_MERGED = "def greet(name, *, punct='!'):\n  return 'hello, ' + name + punct\n"


def _git(repo: Path, *args: str) -> str:
  return subprocess.run(
    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
     "-C", str(repo), *args],
    capture_output=True, text=True, check=True, env=app_git._git_env(repo),
  ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
  for name, text in files.items():
    (repo / name).write_text(text)
  _git(repo, "add", ".")
  _git(repo, "commit", "-q", "-m", msg)
  return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, *, merged: str = _MERGED):
  """base -> draft -> unrelated local work on main; the revised PR merged upstream."""
  repo = tmp_path / "platform"
  repo.mkdir()
  _git(repo, "init", "-q", "-b", "main")
  base = _commit(repo, {"greet.py": _BASE, "notes.md": "v1\n"}, "base")
  draft = _commit(repo, {"greet.py": _DRAFT}, "draft of the contribution")
  local = _commit(repo, {"local.py": "OWNER = True\n"}, "unrelated local work")
  _git(repo, "checkout", "-q", "-b", "release", base)
  merge = _commit(repo, {"greet.py": merged}, "reviewed contribution (#1)")
  release = _commit(repo, {"notes.md": "v2\n"}, "later upstream work")
  _git(repo, "checkout", "-q", "main")
  return repo, base, draft, local, merge, release


def _mark(repo, base, draft, merge, **kw):
  return app_git.mark_draft_superseded(
    repo, contribution_id="rec-1", base_sha=base, head_sha=draft,
    upstream_sha=merge, **kw,
  )


def _show(repo, tree, path):
  return _git(repo, "show", f"{tree}:{path}") + "\n"


def test_draft_of_a_merged_contribution_is_set_aside_and_local_work_kept(tmp_path):
  repo, base, draft, local, merge, release = _repo(tmp_path)
  assert app_git.merge_refs(repo, local, release).status == "conflict"

  _mark(repo, base, draft, merge)
  result = platform_update._final_tree_merge(repo, local, release)

  assert result.status == "clean"
  assert _show(repo, result.merged_tree_oid, "greet.py") == _MERGED
  assert _show(repo, result.merged_tree_oid, "local.py") == "OWNER = True\n"
  assert _show(repo, result.merged_tree_oid, "notes.md") == "v2\n"


def test_nothing_is_set_aside_before_the_release_carries_the_merge(tmp_path):
  repo, base, draft, local, merge, _release = _repo(tmp_path)
  _mark(repo, base, draft, merge)

  assert app_git.without_superseded_drafts(repo, local, base) == local


def test_local_work_built_on_the_draft_stays_with_the_resolver(tmp_path):
  repo, base, draft, _local, merge, release = _repo(tmp_path)
  edited = _commit(
    repo, {"greet.py": _DRAFT + "\nGREETING = greet('owner')\n"}, "built on draft",
  )
  edited = _commit(repo, {"greet.py": _DRAFT.replace("hello", "hey")}, "edited draft")
  _mark(repo, base, draft, merge)

  assert app_git.without_superseded_drafts(repo, edited, release) == edited
  assert platform_update._final_tree_merge(repo, edited, release).status == "conflict"


def test_review_that_only_added_to_the_draft_still_merges_to_upstreams_version(tmp_path):
  merged = _DRAFT + "\ndef bye(name):\n  return 'bye ' + name\n"
  repo, base, draft, local, merge, release = _repo(tmp_path, merged=merged)
  _mark(repo, base, draft, merge)

  result = platform_update._final_tree_merge(repo, local, release)

  assert result.status == "clean"
  assert _show(repo, result.merged_tree_oid, "greet.py") == merged
  assert _show(repo, result.merged_tree_oid, "local.py") == "OWNER = True\n"


def test_a_draft_a_resolver_kept_is_never_removed_by_a_later_update(tmp_path):
  repo, base, draft, local, merge, release = _repo(tmp_path)
  _mark(repo, base, draft, merge)
  # An earlier update integrated the merge, but its resolver kept the draft.
  _git(repo, "checkout", "-q", "-b", "reconciled", release)
  kept = _commit(repo, {"greet.py": _DRAFT, "local.py": "OWNER = True\n"}, "draft kept")
  _git(repo, "checkout", "-q", "release")
  newer = _commit(repo, {"notes.md": "v3\n"}, "newer release")
  _git(repo, "checkout", "-q", "reconciled")

  assert app_git.without_superseded_drafts(repo, kept, newer) == kept
  result = platform_update._final_tree_merge(repo, kept, newer)
  assert _show(repo, result.merged_tree_oid, "greet.py") == _DRAFT


def test_integration_retires_the_draft_and_its_pin(tmp_path):
  repo, base, draft, _local, merge, release = _repo(tmp_path)
  _git(repo, "update-ref", "refs/mobius/contribution-drafts/rec-1", draft)
  ref = _mark(repo, base, draft, merge, draft_ref="refs/mobius/contribution-drafts/rec-1")

  app_git.retire_landed_equivalent_changes(repo, base)
  assert _git(repo, "for-each-ref", ref)  # not integrated yet: keep it

  app_git.retire_landed_equivalent_changes(repo, release)
  assert _git(repo, "for-each-ref", ref) == ""
  assert _git(repo, "for-each-ref", "refs/mobius/contribution-drafts/") == ""


def test_marking_needs_the_draft_commits_and_a_merge_sha(tmp_path):
  repo, base, draft, _local, merge, _release = _repo(tmp_path)
  assert _mark(repo, draft, base, merge) is None  # base must precede head
  assert _mark(repo, base, draft, "not-a-sha") is None
  assert app_git.mark_draft_superseded(
    repo, contribution_id="x", base_sha="0" * 40, head_sha=draft, upstream_sha=merge,
  ) is None


def test_a_merged_record_hands_over_only_a_diverged_draft(tmp_path):
  from app import github_contributions as gc

  repo, base, draft, _local, merge, _release = _repo(tmp_path)
  record = {"id": "rec-9", "status": "merged", "source_sync": {
    "state": "diverged",
    "draft": {"base_sha": base, "head_sha": draft,
              "ref": "refs/mobius/contribution-drafts/rec-9"},
  }}
  assert gc._mark_superseded_draft(record, repo, merge)
  assert gc._mark_superseded_draft(
    {**record, "source_sync": {**record["source_sync"], "state": "in_source"}},
    repo, merge,
  ) is None
  assert gc._mark_superseded_draft(record, repo, None) is None
  assert gc._mark_superseded_draft({"id": "rec-8"}, repo, merge) is None
