"""Short provider probes own the same post-exit cache lifecycle as turns."""
import asyncio
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app import file_cache, provider_usage, providers
from app.routes import settings


@pytest.mark.parametrize('outcome', ['success', 'error', 'timeout', 'missing'])
def test_version_probe_reclaims_after_exit_without_changing_result(monkeypatch, outcome):
  events = []
  monkeypatch.setattr(settings.shutil, 'which', lambda _: None if outcome == 'missing' else '/tool')

  def run(*args, **kwargs):
    events.append('exit')
    if outcome == 'timeout':
      raise subprocess.TimeoutExpired('codex', 2)
    return SimpleNamespace(returncode=1 if outcome == 'error' else 0, stdout='version\n')

  monkeypatch.setattr(settings.subprocess, 'run', run)
  monkeypatch.setattr(file_cache, 'reclaim_provider_cache_sync', lambda p: events.append(p))
  assert settings._cli_version('codex') == ('version' if outcome == 'success' else None)
  assert events == ([] if outcome == 'missing' else ['exit', 'codex'])


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'error', 'invalid', 'timeout', 'cancel'])
async def test_catalog_probe_reaps_before_cache_advice_on_every_exit(monkeypatch, outcome):
  events = []

  class Process:
    returncode = None

    async def communicate(self):
      if outcome == 'timeout':
        raise asyncio.TimeoutError()
      if outcome == 'cancel':
        raise asyncio.CancelledError()
      self.returncode = 1 if outcome == 'error' else 0
      events.append('exit')
      return (b'bad' if outcome == 'invalid' else b'{"models":[{"slug":"test-model"}]}', b'failed')

    def kill(self):
      events.append('kill')

    async def wait(self):
      self.returncode = -9
      events.append('reaped')

  proc = Process()

  async def spawn(*args, **kwargs):
    return proc

  def reclaim(provider):
    assert proc.returncode is not None
    assert threading.current_thread().name.startswith('mobius-model-cache')
    events.append(provider)

  monkeypatch.setattr(providers.shutil, 'which', lambda _: '/codex')
  monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
  monkeypatch.setattr(file_cache, 'reclaim_provider_cache_sync', reclaim)
  call = providers._fetch_codex_models_from_cli('/unused')
  if outcome == 'success':
    assert await call == [{'id': 'test-model'}]
  else:
    with pytest.raises(asyncio.CancelledError if outcome == 'cancel' else RuntimeError):
      await call
  assert events == (['kill', 'reaped', 'codex'] if outcome in ('timeout', 'cancel') else ['exit', 'codex'])


@pytest.mark.asyncio
async def test_catalog_cleanup_ignores_saturated_live_turn_executor(monkeypatch):
  class Process:
    returncode = 0

    async def communicate(self):
      return b'{"models":[{"slug":"test-model"}]}', b''

  async def spawn(*_args, **_kwargs):
    return Process()

  loop = asyncio.get_running_loop()
  occupied = threading.Event()
  release = threading.Event()
  saturated = ThreadPoolExecutor(max_workers=1)
  replacement = ThreadPoolExecutor(max_workers=1)
  loop.set_default_executor(saturated)
  blocker = loop.run_in_executor(None, lambda: (occupied.set(), release.wait()))
  while not occupied.is_set():
    await asyncio.sleep(0)
  advised = []
  monkeypatch.setattr(providers.shutil, 'which', lambda _: '/codex')
  monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
  monkeypatch.setattr(file_cache, 'reclaim_provider_cache_sync',
                      lambda provider: advised.append((provider, threading.current_thread().name)))
  try:
    assert await asyncio.wait_for(providers._fetch_codex_models_from_cli('/unused'), 1) == [
      {'id': 'test-model'},
    ]
    assert advised[0][0] == 'codex'
    assert advised[0][1].startswith('mobius-model-cache')
  finally:
    release.set()
    await blocker
    loop.set_default_executor(replacement)
    saturated.shutdown(wait=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('probe', ['usage', 'interaction'])
@pytest.mark.parametrize('outcome', ['success', 'error', 'timeout', 'cancel'])
async def test_account_probe_closes_then_reclaims_on_owned_worker(monkeypatch, tmp_path, probe, outcome):
  import openai_codex.client as sdk

  events = []
  closed = threading.Event()

  class Client:
    def __init__(self, _config):
      pass

    def close(self):
      events.append('closed')
      closed.set()

  def work(client):
    if outcome == 'error':
      raise RuntimeError('original')
    if outcome == 'cancel':
      raise asyncio.CancelledError()
    if outcome == 'timeout':
      assert closed.wait(2)
    return ('account', 'limits')

  def reclaim(provider):
    assert closed.is_set()
    assert threading.current_thread().name.startswith('mobius-codex-usage')
    events.append('cache')

  async def acquire(_):
    return SimpleNamespace(release=lambda: events.append('released'))

  monkeypatch.setattr(sdk, 'CodexClient', Client)
  monkeypatch.setattr(provider_usage.shutil, 'which', lambda _: '/codex')
  monkeypatch.setattr(provider_usage, '_PROVIDER_TIMEOUT_SECONDS', .02 if outcome == 'timeout' else 2)
  monkeypatch.setattr(provider_usage, 'normalize_codex_usage', lambda *a, **kw: 'normalized')
  monkeypatch.setattr(provider_usage, '_codex_plan_type', lambda _: None)
  monkeypatch.setattr(file_cache, 'reclaim_provider_cache_sync', reclaim)
  monkeypatch.setattr('app.codex_session_lock.acquire_codex_session_activity_async', acquire)

  class Limits:
    def model_dump(self, **kwargs):
      return {}

  def read(client):
    work(client)
    return None, Limits()

  monkeypatch.setattr(provider_usage, '_read_codex_client', read)
  call = (provider_usage._fetch_codex_usage(str(tmp_path)) if probe == 'usage' else
          provider_usage._run_on_codex_client(str(tmp_path), work, timeout_error='timeout'))
  if outcome == 'success':
    assert await call == ('normalized' if probe == 'usage' else ('account', 'limits'))
  else:
    with pytest.raises(asyncio.CancelledError if outcome == 'cancel' else RuntimeError):
      await call
  assert events[-2:] == (['cache', 'released'] if probe == 'usage' else ['closed', 'cache'])


def test_sync_advice_failure_never_masks_probe_result(monkeypatch):
  def fail(_):
    raise OSError('unavailable')
  monkeypatch.setattr(file_cache, 'provider_tool_paths', fail)
  assert file_cache.reclaim_provider_cache_sync('codex') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('probe', ['usage', 'interaction'])
async def test_stubborn_account_worker_skips_post_exit_advice(monkeypatch, tmp_path, probe):
  import openai_codex.client as sdk

  release = threading.Event()
  closed = threading.Event()
  advised = []

  class Client:
    def __init__(self, _config):
      pass

    def close(self):
      closed.set()

  def work(_client):
    release.wait(5)
    return None, None

  async def acquire(_):
    return SimpleNamespace(release=lambda: None)

  monkeypatch.setattr(sdk, 'CodexClient', Client)
  monkeypatch.setattr(provider_usage.shutil, 'which', lambda _: '/codex')
  monkeypatch.setattr(provider_usage, '_PROVIDER_TIMEOUT_SECONDS', .01)
  monkeypatch.setattr(provider_usage, '_read_codex_client', work)
  monkeypatch.setattr(file_cache, 'reclaim_provider_cache_sync', lambda _: advised.append(True))
  monkeypatch.setattr('app.codex_session_lock.acquire_codex_session_activity_async', acquire)
  try:
    call = (provider_usage._fetch_codex_usage(str(tmp_path)) if probe == 'usage' else
            provider_usage._run_on_codex_client(str(tmp_path), work, timeout_error='timed out'))
    with pytest.raises(RuntimeError, match='timed out'):
      await call
    assert closed.is_set()
    assert advised == []
  finally:
    release.set()
