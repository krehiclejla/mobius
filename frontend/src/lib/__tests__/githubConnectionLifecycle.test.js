/* Resuming sign-in is an activation action, not a side effect of ending a wait. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as tick } from 'node:timers/promises'
import { api } from '../../api/client.js'
import GithubConnection from '../../components/SettingsView/GithubConnection.jsx'
import { renderHook } from '../../components/ChatView/hooks/__tests__/react-hook-shim.mjs'

const json = body => new Response(JSON.stringify(body), { status: 200 })
const attempt = { attempt_id: 'a1', user_code: 'TEST-CODE', verification_uri: 'https://github.com/login/device' }

for (const connected of [false, true]) {
  test(`cancel a resumed ${connected ? 'private-access upgrade' : 'sign-in'} without restarting its wait`, async t => {
    const original = api.github
    t.after(() => { api.github = original })
    let polls = 0
    let cancelled = false
    let releaseCancel
    api.github = {
      status: async () => json({ connected, device_flow_available: true, active_attempt: cancelled ? null : attempt }),
      connectPoll: async () => { polls++; return json({ status: 'pending', retry_after: 100 }) },
      connectCancel: async id => {
        assert.equal(id, 'a1')
        await new Promise(resolve => { releaseCancel = resolve })
        cancelled = true
        return json({ status: 'cancelled' })
      },
    }
    const view = renderHook(() => GithubConnection({ active: true }))
    t.after(() => view.unmount())
    await tick(20)
    const panel = view.result.current.props.children.props.children
    assert.equal(panel.props.attempt.attemptId, 'a1')
    assert.equal(polls, 1)
    const cancellation = panel.props.onCancel()
    await tick(20)
    assert.equal(polls, 1, 'cancelling must not resume the cached attempt')
    releaseCancel()
    await cancellation
    await tick(20)
    assert.equal(polls, 1)
    assert.equal(view.result.current.props.children.props.children.props.attempt, undefined)
  })
}

test('a failed cancellation keeps the server-owned attempt visible and resumes its wait', async t => {
  const original = api.github
  t.after(() => { api.github = original })
  let polls = 0
  api.github = {
    status: async () => json({ connected: false, device_flow_available: true, active_attempt: attempt }),
    connectPoll: async () => { polls++; return json({ status: 'pending', retry_after: 100 }) },
    connectCancel: async () => { throw new Error('Cancellation service unavailable') },
  }
  const view = renderHook(() => GithubConnection({ active: true }))
  t.after(() => view.unmount())
  await tick(20)
  await view.result.current.props.children.props.children.props.onCancel()
  await tick(20)
  const panel = view.result.current.props.children.props.children
  assert.equal(panel.props.attempt?.attemptId, 'a1')
  assert.equal(panel.props.message, 'Cancellation service unavailable')
  assert.equal(polls, 2, 'the still-active server attempt is observed again')
})

test('unconfirmed cancellation never hides the device code when status also fails', async t => {
  const original = api.github
  t.after(() => { api.github = original })
  let unavailable = false
  api.github = {
    status: async () => {
      if (unavailable) throw new Error('offline')
      return json({ connected: false, device_flow_available: true, active_attempt: attempt })
    },
    connectPoll: async () => json({ status: 'pending', retry_after: 100 }),
    connectCancel: async () => { unavailable = true; throw new Error('offline') },
  }
  const view = renderHook(() => GithubConnection({ active: true }))
  t.after(() => view.unmount())
  await tick(20)
  await view.result.current.props.children.props.children.props.onCancel()
  const panel = view.result.current.props.children.props.children
  assert.equal(panel.props.attempt?.attemptId, 'a1')
  assert.equal(panel.props.message, 'offline')
  assert.equal(panel.props.cancelling, false)
})

test('unmount during start aborts its request and cannot create a later orphan poll', async t => {
  const original = api.github
  t.after(() => { api.github = original })
  let releaseStart, startSignal
  let polls = 0
  api.github = {
    status: async () => json({ connected: false, device_flow_available: true }),
    connectStart: async (_private, options) => {
      startSignal = options.signal
      await new Promise(resolve => { releaseStart = resolve })
      return json(attempt)
    },
    connectPoll: async () => { polls++; return json({ status: 'complete' }) },
  }
  const view = renderHook(() => GithubConnection({ active: true }))
  await tick(20)
  const panel = view.result.current.props.children.props.children
  const connect = panel.props.children[2].props.children
  const starting = connect.props.onClick()
  view.unmount()
  releaseStart()
  await starting
  assert.equal(startSignal?.aborted, true)
  assert.equal(polls, 0)
})

test('unmount during cancellation owns reconciliation and cannot resume a hidden poll', async t => {
  const original = api.github
  t.after(() => { api.github = original })
  let releaseCancel, cancelSignal
  let polls = 0
  api.github = {
    status: async () => json({ connected: false, device_flow_available: true, active_attempt: attempt }),
    connectPoll: async () => { polls++; return json({ status: 'pending', retry_after: 100 }) },
    connectCancel: async (_id, options) => {
      cancelSignal = options.signal
      await new Promise(resolve => { releaseCancel = resolve })
      throw new Error('offline')
    },
  }
  const view = renderHook(() => GithubConnection({ active: true }))
  await tick(20)
  const cancelling = view.result.current.props.children.props.children.props.onCancel()
  view.unmount()
  releaseCancel()
  await cancelling
  await tick(20)
  assert.equal(cancelSignal?.aborted, true)
  assert.equal(polls, 1, 'only the original visible poll may run')
})
