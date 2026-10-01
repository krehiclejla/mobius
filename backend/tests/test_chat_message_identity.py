"""Physical-run ownership is independent of split-reply and legacy row shape."""

import pytest

from app.chat_message_identity import assistant_message_run_id


@pytest.mark.parametrize("message_id, expected", [
  ("run", "run"),
  ("run:assistant:1", "run"),
  ("run:assistant:123", "run"),
  ("run:assistant:0", "run:assistant:0"),
  ("run:assistant:01", "run:assistant:01"),
  ("run:assistant:-1", "run:assistant:-1"),
  ("run:assistant:notes", "run:assistant:notes"),
  ("run:assistant:١", "run:assistant:١"),
  ("run:assistant:1:later", "run:assistant:1:later"),
  (None, None),
  (123, None),
])
def test_only_valid_sink_segments_inherit_the_physical_run(message_id, expected):
  assert assistant_message_run_id(message_id) == expected
