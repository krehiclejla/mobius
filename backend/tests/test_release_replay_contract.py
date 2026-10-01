"""Hermetic contracts for the opt-in disposable real-release replay harness."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess

import pytest
import yaml

from app.manifest_contract import job_interpreter, validate_setup

ROOT = Path(__file__).parents[2]
HARNESS = ROOT / "scripts/test-host-helper.sh"
FIXTURE = ROOT / "scripts/fixtures/release-replay"


@pytest.mark.parametrize("previous,target", [
  ("main", "b" * 40), ("a" * 40, "b" * 40 + '\"'), ("a" * 40, "a" * 40),
])
def test_invalid_release_inputs_stop_before_any_host_command(tmp_path, previous, target):
  # No tools on PATH: an accidental earlier host command would be observable.
  result = subprocess.run(["/bin/bash", str(HARNESS), previous, target],
                          env={"PATH": str(tmp_path)}, capture_output=True, text=True)
  assert result.returncode == 2
  assert result.stderr.strip() == "two distinct full release SHAs are required"


def test_replay_fixture_is_an_accepted_setup_declaration():
  manifest = json.loads((FIXTURE / "mobius.json").read_text())
  validate_setup(manifest)
  for step in manifest["setup"]["steps"]:
    assert job_interpreter((FIXTURE / step).read_bytes())


@pytest.mark.parametrize("version_ok", [True, False])
def test_python_fixture_check_never_installs_packages(tmp_path, version_ok):
  python = tmp_path / "python3"
  python.write_text(f"#!/bin/sh\nexit {0 if version_ok else 1}\n")
  python.chmod(0o755)
  result = subprocess.run(["/bin/sh", str(FIXTURE / "restore-python.sh"), "check"],
                          env={"PATH": str(tmp_path)}, capture_output=True, text=True)
  assert result.returncode == (0 if version_ok else 1)
  assert not result.stderr


def test_python_fixture_apply_uses_only_the_pinned_package(tmp_path):
  sudo = tmp_path / "sudo"
  sudo.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
  sudo.chmod(0o755)
  result = subprocess.run(["/bin/sh", str(FIXTURE / "restore-python.sh"), "apply"],
                          env={"PATH": str(tmp_path)}, capture_output=True, text=True, check=True)
  assert result.stdout.splitlines() == [
    "-n", "python3", "-m", "pip", "install", "--no-cache-dir", "pyfiglet==1.0.4",
  ]


def test_replay_is_manual_opt_in_on_a_disposable_hosted_runner():
  # BaseLoader leaves GitHub's `on` key as a string (rather than YAML 1.1 bool).
  workflow = yaml.load((ROOT / ".github/workflows/test.yml").read_text(), Loader=yaml.BaseLoader)
  option = workflow["on"]["workflow_dispatch"]["inputs"]["release_replay"]
  assert option["type"] == "boolean" and option["default"] == "false"
  job = workflow["jobs"]["release-replay"]
  assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.release_replay"
  assert job["runs-on"] == "ubuntu-24.04"
  assert job["timeout-minutes"] == "60"
  assert workflow["permissions"] == {"contents": "read"}
  assert job["steps"][0]["with"]["persist-credentials"] == "false"
  step = job["steps"][1]
  for sha in step["env"].values():
    assert re.fullmatch("[0-9a-f]{40}", sha)
  assert "MOBIUS_RELEASE_REPLAY=1" in step["run"]


def test_replay_keeps_image_and_source_proof_and_no_postboot_manual_install():
  source = HARNESS.read_text()
  # The required source is prepared through the existing review boundary.
  assert source.index("pu.platform_update_preview") < source.index('nonce=$(queue "$TARGET")')
  assert 'pu.prepare_reviewed_update(**plan)' in source
  assert 'assert not prepared["requires_image"]' in source
  after = source.split('replaced_with "$TARGET"', 1)[1]
  assert "merge-base" in after and 'version["served_sha"]' in after
  assert ".platform-prepared-update.json" in after
  assert "apt-get" not in after and "pip install" not in after
  assert "restore-python.sh apply" not in after and "setup/rerun" not in after.replace(
    "# Never call setup/rerun here: startup alone must restore the declarations.", "")
  assert 'target_revision=$(revision_of "$TARGET")' in source
  assert "target_revision > seeded" in source
  assert '/etc/mobius-rebuild' in source
  assert '/var/lib/mobius-rebuild' in source
  assert 'mobius-rebuild-reconcile.service' in source
  assert 'active["sha256"] == sys.argv[1]' in source


def test_embedded_python_and_shell_are_syntactically_valid():
  subprocess.run(["bash", "-n", str(HARNESS)], check=True)
  subprocess.run(["sh", "-n", str(FIXTURE / "restore-python.sh")], check=True)
  source = HARNESS.read_text()
  for label in ("SOURCE", "VERIFY", "WORKER_VERIFY"):
    body = source.split("<<'" + label + "'\n", 1)[1].split("\n" + label, 1)[0]
    compile(body, str(HARNESS) + ":" + label, "exec")
