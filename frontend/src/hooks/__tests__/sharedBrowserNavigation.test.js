/* A shared shell keeps its route and navigation state out of owner storage. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { renderHook } from '../../components/ChatView/hooks/__tests__/react-hook-shim.mjs'
import { sharedBrowserWorkspaceStorage } from '../../lib/sharedBrowserWorkspace.js'

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    values,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  }
}

test('shared navigation stays on /shell/shared and never reads or writes owner storage', async () => {
  const owner = memoryStorage({ token: 'owner-secret', moebius_active_chat: 'owner-chat' })
  const ownerReads = []
  globalThis.localStorage = {
    getItem: key => { ownerReads.push(key); return owner.getItem(key) },
    setItem: () => assert.fail('owner localStorage write'),
    removeItem: () => assert.fail('owner localStorage delete'),
  }
  const session = memoryStorage({ 'shell-reload': 'owner-snapshot' })
  globalThis.sessionStorage = session
  const replaced = []
  globalThis.history = {
    state: null,
    pushState(state) { this.state = state },
    replaceState(state, _title, url) { this.state = state; replaced.push(url) },
    back() {},
  }
  globalThis.window = {
    location: { pathname: '/shell/shared', search: '?chat=guest-target', href: 'https://example.test/shell/shared?chat=guest-target' },
    innerWidth: 390, innerHeight: 844,
    addEventListener() {}, removeEventListener() {},
  }
  globalThis.location = window.location
  globalThis.document = { body: { style: {} } }
  delete globalThis.navigation

  const [{ default: useNavigation }, paneModel] = await Promise.all([
    import('../useNavigation.js'),
    import('../../components/Shell/paneModel.js'),
  ])
  const guestStorage = sharedBrowserWorkspaceStorage('grant-1', session)
  const ws = paneModel.seedFromFlatTabs([{ kind: 'chat', id: 'guest-chat' }])
  const workspaceStateRef = { current: { ws, undo: null } }
  renderHook(useNavigation, {
    workspace: ws, workspaceStateRef,
    dispatchWorkspace: action => {
      workspaceStateRef.current = paneModel.workspaceReducer(workspaceStateRef.current, action)
    },
    visiblePaneIds: new Set(Object.keys(ws.panes)),
    blobValid: true,
    replaceImplicitBootTab: false,
    dragActiveRef: { current: false },
    navigationStorage: guestStorage,
    routePath: '/shell/shared',
  })
  assert.equal(replaced[0], '/shell/shared')
  assert.equal(session.getItem('shell-reload'), 'owner-snapshot')
  assert.equal(owner.getItem('token'), 'owner-secret')
  assert.equal(owner.getItem('moebius_active_chat'), 'owner-chat')
  assert.deepEqual(ownerReads, [])
  assert.equal(guestStorage.getItem('moebius_active_view'), 'chat')
  assert.equal(paneModel.activeContentRoute(workspaceStateRef.current.ws).chatId, 'guest-target')
})
