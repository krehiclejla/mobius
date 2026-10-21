/* The Möbius provider page reads model access; the account itself lives in Möbius · You. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as tick } from 'node:timers/promises'
import MobiusProviderAccess from '../../components/SettingsView/MobiusProviderAccess.jsx'
import { parseAgentAccess } from '../../components/SettingsView/mobiusAgentAccess.js'
import { renderHook } from '../../components/ChatView/hooks/__tests__/react-hook-shim.mjs'

const json = body => new Response(JSON.stringify(body), { status: 200 })
const signedOut = { agent_access: 'signed_out', models: [], balance: {}, trial: {}, retention: {} }
const available = {
  agent_access: 'available',
  models: [{ id: 'evolve', name: 'Evolve', pricing: { input: 1, cached_input: 0.1, output: 4 } }],
  balance: { available_units: 5_000_000 },
  trial: { state: 'active' },
  retention: { policy: 'local-testing-v1', notice: 'Requests are retained briefly for local testing.' },
}

function findNode(node, matches) {
  if (Array.isArray(node)) return node.map(child => findNode(child, matches)).find(Boolean)
  if (!node || typeof node !== 'object') return null
  if (matches(node)) return node
  return findNode(node.props?.children, matches)
}
const findButton = (node, label) => findNode(node, child =>
  child.type === 'button' && child.props?.children === label)
const accessLabel = node => findNode(node, child =>
  child.props?.className === 'settings-mobius-access__row').props.children[1].props.children

function serve(t, accessResponses) {
  const requests = []
  t.mock.method(globalThis, 'fetch', async url => {
    requests.push(url)
    if (url.endsWith('/api/auth/providers/mobius/enabled')) return json({ enabled: true })
    if (url.endsWith('/api/identity/agent')) return json(accessResponses.shift())
    throw new Error(`unexpected request ${url}`)
  })
  return requests
}

test('a signed-out Möbius provider page offers sign-in through Möbius · You', async t => {
  serve(t, [signedOut])
  let opened = 0
  const view = renderHook(props => MobiusProviderAccess(props), { active: true, onOpenAccount: () => { opened++ } })
  t.after(() => view.unmount())
  await tick(20)
  assert.equal(accessLabel(view.result.current), 'Sign in needed')
  findButton(view.result.current, 'Sign in from Möbius · You').props.onClick()
  assert.equal(opened, 1)
})

test('returning to Settings after signing in elsewhere re-reads model access', async t => {
  const requests = serve(t, [signedOut, available])
  const props = { active: true, onOpenAccount: () => {} }
  const view = renderHook(next => MobiusProviderAccess(next), props)
  t.after(() => view.unmount())
  await tick(20)
  view.rerender({ ...props, active: false })
  view.rerender(props)
  await tick(20)
  assert.equal(requests.filter(url => url.endsWith('/api/identity/agent')).length, 2)
  assert.equal(accessLabel(view.result.current), 'Available')
  assert.ok(!findButton(view.result.current, 'Sign in from Möbius · You'))
})

test('signed-out model access may not carry prices, balance or trial data', () => {
  assert.deepEqual(parseAgentAccess(signedOut), signedOut)
  assert.throws(() => parseAgentAccess({ ...signedOut, models: available.models }), /without account access/)
  assert.throws(() => parseAgentAccess({ ...signedOut, balance: { available_units: 1 } }), /without account access/)
  assert.throws(() => parseAgentAccess({ ...signedOut, trial: { state: 'ready' } }), /without account access/)
})

test('unknown fields added by the identity service are ignored at every level', () => {
  const extended = {
    ...available,
    region: 'eu',
    models: [{ ...available.models[0], context_window: 200000, tier: 'pro', pricing: { ...available.models[0].pricing, batch: 0.5 } }],
    balance: { ...available.balance, available_usd: '5.00', currency: 'USD' },
    trial: { ...available.trial, expires_at: '2030-01-01T00:00:00Z' },
    retention: { ...available.retention, policy: 'a-new-policy', days: 30 },
  }
  assert.deepEqual(parseAgentAccess(extended), {
    agent_access: 'available',
    models: [{ id: 'evolve', name: 'Evolve', pricing: { input: 1, cached_input: 0.1, output: 4 } }],
    balance: { available_units: 5_000_000 },
    trial: { state: 'active' },
    retention: { notice: available.retention.notice },
  })
  assert.deepEqual(parseAgentAccess({ ...signedOut, hint: 'sign in' }), signedOut)
})

test('fields the page renders are still validated', () => {
  assert.throws(() => parseAgentAccess({ ...available, agent_access: 'maybe' }), /invalid model access/)
  assert.throws(() => parseAgentAccess({ ...available, models: [{ id: 'evolve', name: 'Evolve', pricing: { input: -1, cached_input: 0, output: 0 } }] }), /invalid model prices/)
  assert.throws(() => parseAgentAccess({ ...available, models: [available.models[0], available.models[0]] }), /duplicate/)
  assert.throws(() => parseAgentAccess({ ...available, balance: { available_units: 1.5 } }), /invalid model balance/)
  assert.throws(() => parseAgentAccess({ ...available, trial: {} }), /invalid trial state/)
  assert.throws(() => parseAgentAccess({ ...available, retention: { policy: 'x' } }), /retention/)
})

test('trial activation asks for confirmation before it changes the account', async t => {
  const posts = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    if (url.endsWith('/api/auth/providers/mobius/enabled')) return json({ enabled: true })
    if (url.endsWith('/api/identity/agent/trial')) {
      posts.push(options.method)
      return json({ ...available, trial: { state: 'active' } })
    }
    if (url.endsWith('/api/identity/agent')) return json({ ...available, trial: { state: 'ready' } })
    throw new Error(`unexpected request ${url}`)
  })
  const view = renderHook(props => MobiusProviderAccess(props), { active: true })
  t.after(() => view.unmount())
  await tick(20)

  findButton(view.result.current, 'Activate trial credit…').props.onClick()
  assert.deepEqual(posts, [])
  findButton(view.result.current, 'Cancel').props.onClick()
  assert.deepEqual(posts, [])
  assert.ok(findButton(view.result.current, 'Activate trial credit…'))

  findButton(view.result.current, 'Activate trial credit…').props.onClick()
  await findButton(view.result.current, 'Activate').props.onClick()
  await tick(20)
  assert.deepEqual(posts, ['POST'])
  assert.ok(!findButton(view.result.current, 'Activate trial credit…'))
  assert.ok(!findButton(view.result.current, 'Activate'))
})

test('leaving Settings drops a pending trial confirmation', async t => {
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith('/api/auth/providers/mobius/enabled')) return json({ enabled: true })
    if (url.endsWith('/api/identity/agent')) return json({ ...available, trial: { state: 'ready' } })
    throw new Error(`unexpected request ${url}`)
  })
  const props = { active: true }
  const view = renderHook(next => MobiusProviderAccess(next), props)
  t.after(() => view.unmount())
  await tick(20)
  findButton(view.result.current, 'Activate trial credit…').props.onClick()
  view.rerender({ active: false })
  view.rerender(props)
  await tick(20)
  assert.ok(findButton(view.result.current, 'Activate trial credit…'))
  assert.ok(!findButton(view.result.current, 'Activate'))
})
