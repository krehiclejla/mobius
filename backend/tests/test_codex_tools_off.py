import re
from pathlib import Path

from app.providers import CODEX_TOOLS_OFF_VERIFIED_FOR


DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def test_codex_tools_off_list_was_traced_on_the_pinned_codex():
  # Codex silently ignores a feature switch it does not know, so a version
  # bump can turn a tools-off switch into a no-op. Re-trace the list on the
  # new Codex (see CODEX_TOOLS_OFF in app/providers.py), then update
  # CODEX_TOOLS_OFF_VERIFIED_FOR.
  pinned = re.search(
    r"^ARG CODEX_VERSION=(\S+)$", DOCKERFILE.read_text(encoding="utf-8"), re.M,
  )
  assert pinned, "Dockerfile no longer pins CODEX_VERSION"
  assert pinned[1] == CODEX_TOOLS_OFF_VERIFIED_FOR, (
    f"Codex {pinned[1]} is pinned, but the tools-off list was traced on "
    f"{CODEX_TOOLS_OFF_VERIFIED_FOR}"
  )
