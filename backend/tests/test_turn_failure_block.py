"""Provider turn failures read in plain words, with Resume where it helps."""

from app.chat import (
  PROCESS_DIED_MESSAGE,
  TURN_STOPPED_MESSAGE,
  turn_failure_block,
)


def test_interrupted_execution_becomes_a_resumable_plain_stop():
  block = turn_failure_block("Execution interrupted.")
  assert block == {
    "type": "error", "message": TURN_STOPPED_MESSAGE, "resumable": True,
  }


def test_killed_provider_process_hides_exit_codes_behind_a_plain_message():
  raw = (
    "Command failed with exit code -6 (exit code: -6) Error output: Check "
    "stderr output for details\n(no stderr captured — the CLI was likely "
    "killed before writing output, e.g. OOM or timeout)"
  )
  block = turn_failure_block(raw)
  assert block == {
    "type": "error", "message": PROCESS_DIED_MESSAGE, "resumable": True,
  }
  assert "exit code" not in block["message"]


def test_other_provider_messages_stay_verbatim_and_not_resumable():
  raw = "API Error: safeguards flagged this message. Try rephrasing."
  assert turn_failure_block(raw) == {"type": "error", "message": raw}
  # A positive exit status is an ordinary command result, not a killed process.
  ordinary = "Command failed with exit code 1"
  assert turn_failure_block(ordinary) == {"type": "error", "message": ordinary}
