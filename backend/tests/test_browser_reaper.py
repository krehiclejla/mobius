"""Browsers left by turns that died with the server are closed, and chat
browsers are held to a memory budget, without touching a live turn's browser
unless the budget forces it."""
import asyncio

import pytest

from app import chat


class _Registry:
  def __init__(self, alive=()):
    self.alive = set(alive)

  def is_alive(self, chat_id):
    return chat_id in self.alive


@pytest.fixture
def reaper(monkeypatch):
  closed = []
  state = {"usage": {}, "registry": _Registry()}

  async def close(chat_id):
    closed.append(chat_id)

  monkeypatch.setattr(chat, "_close_browser_session", close)
  monkeypatch.setattr(chat, "registry", state["registry"])
  monkeypatch.setattr(
    chat.browser_processes, "browser_memory_by_chat",
    lambda **_: dict(state["usage"]),
  )
  state["closed"] = closed
  return state


def _run(budget=None):
  return asyncio.run(chat.reap_unowned_browsers(memory_budget_bytes=budget))


def test_browser_of_a_turn_that_died_with_the_server_is_closed(reaper):
  reaper["usage"] = {"dead": 2_600_000_000, "live": 100}
  reaper["registry"].alive.add("live")
  result = _run()
  assert reaper["closed"] == ["dead"]
  assert result == {"orphans_closed": {"dead": 2_600_000_000}, "guard_closed": {}}


def test_turn_starting_while_reaper_waits_for_the_lock_keeps_its_browser(reaper):
  reaper["usage"] = {"starting": 100}

  async def scenario():
    lock = chat._browser_lifecycle_lock("starting")
    await lock.acquire()
    task = asyncio.create_task(chat.reap_unowned_browsers(memory_budget_bytes=None))
    await asyncio.sleep(0)
    reaper["registry"].alive.add("starting")
    lock.release()
    return await task

  assert asyncio.run(scenario()) == {"orphans_closed": {}, "guard_closed": {}}
  assert reaper["closed"] == []


def test_memory_guard_closes_largest_live_browsers_until_under_budget(reaper):
  reaper["usage"] = {"small": 300, "huge": 2_000, "medium": 900}
  reaper["registry"].alive.update({"small", "huge", "medium"})
  result = _run(budget=1_500)
  assert reaper["closed"] == ["huge"]
  assert result["guard_closed"] == {"huge": 2_000}


def test_memory_guard_counts_orphans_already_closed(reaper):
  reaper["usage"] = {"dead": 5_000, "live": 1_000}
  reaper["registry"].alive.add("live")
  result = _run(budget=1_500)
  assert reaper["closed"] == ["dead"]
  assert result["guard_closed"] == {}
