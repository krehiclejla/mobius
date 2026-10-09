"""The suite cannot inherit the deployment identity of the host it runs on."""

import os

from app import platform_activation


def test_suite_does_not_inherit_host_railway_identity():
  # Möbius instances hosted on Railway carry RAILWAY_* variables. A test run
  # started inside one must still see the self-hosted default unless a test
  # opts into Railway explicitly.
  assert not [name for name in os.environ if name.startswith("RAILWAY_")]
  assert platform_activation.deployment_kind() == "self_hosted"
