"""Setup declarations are paths and native Debian dependency strings."""

import pytest

from app.manifest_contract import (
  ManifestContractError, validate_manifest_contract, validate_setup,
)


def _manifest(**updates):
  return {
    "id": "setup-test", "name": "Setup test", "version": "1",
    "description": "test", "entry": "index.jsx",
    "source_files": ["scripts/install.sh"],
    **updates,
  }


@pytest.mark.parametrize("setup", [
  {},
  {"steps": ["scripts/install.sh"]},
  {"apt": ["ffmpeg", "libssl-dev", "g++", "foo (>= 2), foo (<< 4) | bar"]},
  {"steps": ["scripts/install.sh"], "apt": ["libssl-dev"]},
])
def test_setup_accepts_optional_lists(setup):
  validate_manifest_contract(_manifest(setup=setup))
  validate_setup({"source_files": ["scripts/install.sh"], "setup": setup})


@pytest.mark.parametrize("setup", [
  None, [], {"extra": True}, {"steps": "scripts/install.sh"},
  {"steps": ["../install.sh"]},
  {"steps": [{"path": "scripts/install.sh"}]},
  {"apt": "ffmpeg"}, {"apt": ["-y"]}, {"apt": [" "]},
  {"apt": ["foo\x00bar"]}, {"apt": ["foo\nbar"]},
])
def test_setup_rejects_other_shapes_commands_and_uncovered_paths(setup):
  with pytest.raises(ManifestContractError):
    validate_manifest_contract(_manifest(setup=setup))
  with pytest.raises(ManifestContractError):
    validate_setup({"source_files": ["scripts/install.sh"], "setup": setup})


@pytest.mark.parametrize("step", ["scripts/missing.sh", "scripts/install.sh --flag"])
def test_setup_source_membership_belongs_to_install_contract_only(step):
  manifest = _manifest(setup={"steps": [step]})
  validate_setup(manifest)
  with pytest.raises(ManifestContractError, match="source_files"):
    validate_manifest_contract(manifest)


def test_setup_can_validate_without_other_manifest_fields():
  validate_setup({})
  validate_setup({"setup": {"steps": ["scripts/install.sh"]}})
