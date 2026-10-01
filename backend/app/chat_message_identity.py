"""Pure assistant-message identity matching shared by chat read/write paths."""

from __future__ import annotations

import re


def assistant_message_run_id(message_id: object) -> str | None:
  """Read physical ownership from an exact root or valid sink segment id.

  Only the reserved positive ASCII segment suffix inherits a root. Malformed
  suffixes remain exact identities; id-less legacy rows have no run identity.
  Placement callers retain their event-specific position and fallback rules.
  """
  if not isinstance(message_id, str):
    return None
  return re.sub(r":assistant:[1-9][0-9]*$", "", message_id)


def assistant_message_index(messages: list, message: dict) -> int:
  """Locate the assistant row owned by ``message``.

  Current snapshots carry the segment's durable ``id`` and may update that
  exact row even when a hidden same-turn answer follows it. Id-less snapshots
  are historical/test compatibility only and retain the former trailing-row
  rule. One rolling-upgrade exception lets an explicit id adopt a trailing
  id-less assistant; it can never cross a later turn or a different id.
  """
  message_id = message.get("id") if isinstance(message, dict) else None
  if message_id is not None:
    target = str(message_id)
    for index, candidate in enumerate(messages):
      if (
        isinstance(candidate, dict)
        and candidate.get("role") == "assistant"
        and candidate.get("id") is not None
        and str(candidate.get("id")) == target
      ):
        return index
    if (
      messages
      and messages[-1].get("role") == "assistant"
      and messages[-1].get("id") is None
    ):
      # Rolling upgrade only: the former persistence shape has no identity to
      # compare. A trailing id-less assistant is the one safe adoption point;
      # any explicit id or later visible turn proves this is a new segment.
      return len(messages) - 1
    return -1
  if messages and messages[-1].get("role") == "assistant":
    return len(messages) - 1
  return -1
