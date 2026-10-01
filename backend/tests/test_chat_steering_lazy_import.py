"""Idle chat admission must not load provider runtimes just to find no turn."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module_name", ["app.chat_steering", "app.main"])
def test_idle_steering_keeps_provider_runtimes_unloaded(module_name):
  backend = Path(__file__).resolve().parents[1]
  code = f"""
import sys
__import__({module_name!r})
from app.chat_steering import has_live_steerable_turn
if {module_name!r} == 'app.main':
    from app.routes import require_all_routers_loaded
    require_all_routers_loaded()
for provider in ('claude', 'codex'):
    assert not has_live_steerable_turn('idle-chat', provider)
for name in ('app.claude_sdk_runner', 'app.codex_sdk_runner',
             'claude_agent_sdk', 'openai_codex', 'mcp'):
    assert name not in sys.modules, name
"""
  env = os.environ.copy()
  env['PYTHONPATH'] = str(backend) + os.pathsep + env.get('PYTHONPATH', '')
  result = subprocess.run(
    [sys.executable, '-c', code], cwd=backend, env=env,
    capture_output=True, text=True, timeout=30,
  )
  assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("steerable", [False, True])
def test_lazy_steering_preserves_claude_safe_boundary_admission(monkeypatch, steerable):
  from app import chat_steering, claude_sdk_runner

  class ActiveClient:
    is_steerable = steerable

  monkeypatch.setattr(claude_sdk_runner, "ActiveClaudeClient", ActiveClient)
  monkeypatch.setattr(chat_steering.registry, "get_handle", lambda *_args: ActiveClient())
  assert chat_steering.has_live_steerable_turn("active-chat", "claude") is steerable
