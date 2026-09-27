// The Shell's system stream owns the live drawer-list read; a mount-time refetch
// of a persisted list would start before its subscription and be discarded.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { api } from '../../../api/client.js'
import { letSystemStreamOwnListRefresh } from '../shellListReconciliation.js'
import { chatQueries, appQueries } from '../../../hooks/queries.js'

function countingList(t, kind) {
  const calls = { count: 0 }
  t.mock.method(api[kind], 'list', async () => {
    calls.count += 1
    return { ok: true, json: async () => [] }
  })
  return calls
}

function mount(client, queries) {
  const observer = new QueryObserver(client, {
    queryKey: queries.keys.all,
    queryFn: context => queries.list.fetch(context),
  })
  return observer.subscribe(() => {})
}

const settle = () => new Promise(resolve => setTimeout(resolve, 20))

for (const [kind, queries] of [['chats', chatQueries], ['apps', appQueries]]) {
  test(`${kind}: a stale persisted list does not refetch on mount once the stream owns it`, async t => {
    const client = new QueryClient()
    letSystemStreamOwnListRefresh(client, [chatQueries, appQueries])
    client.setQueryData(queries.keys.all, [{ id: 'persisted' }], { updatedAt: 1 })
    const calls = countingList(t, kind)
    const unsubscribe = mount(client, queries)
    try {
      await settle()
      assert.equal(calls.count, 0)
    } finally { unsubscribe(); client.clear() }
  })

  test(`${kind}: a list with no cached data still loads on mount`, async t => {
    const client = new QueryClient()
    letSystemStreamOwnListRefresh(client, [chatQueries, appQueries])
    const calls = countingList(t, kind)
    const unsubscribe = mount(client, queries)
    try {
      await settle()
      assert.equal(calls.count, 1)
    } finally { unsubscribe(); client.clear() }
  })
}

test('stream ownership reaches only the two drawer lists', () => {
  const client = new QueryClient()
  letSystemStreamOwnListRefresh(client, [chatQueries, appQueries])
  assert.equal(client.getQueryDefaults(chatQueries.keys.all).refetchOnMount, false)
  assert.equal(client.getQueryDefaults(appQueries.keys.all).refetchOnMount, false)
  assert.equal(client.getQueryDefaults(chatQueries.keys.messages('c1')).refetchOnMount, undefined)
  assert.equal(client.getQueryDefaults(appQueries.keys.token(7)).refetchOnMount, undefined)
  assert.equal(client.getQueryDefaults(['projects']).refetchOnMount, undefined)
})
