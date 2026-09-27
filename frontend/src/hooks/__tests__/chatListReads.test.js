import test from 'node:test'
import assert from 'node:assert/strict'

test('a complete list read aborted after it decodes does not count as landed', async () => {
  const controller = new AbortController()
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    // The replaced reconnect finishes decoding only after its abort.
    json: async () => { controller.abort(); return [] },
    text: async () => { controller.abort(); return '[]' },
    clone() { return this },
  })
  try {
    const { chatQueries } = await import('../queries.js')
    const mark = chatQueries.list.readMark()
    await assert.rejects(chatQueries.list.fetch({ signal: controller.signal }))
    assert.equal(chatQueries.list.readLandedSince(mark), false)

    const landedMark = chatQueries.list.readMark()
    await chatQueries.list.fetch({ signal: new AbortController().signal })
    assert.equal(chatQueries.list.readLandedSince(landedMark), true)
  } finally {
    globalThis.fetch = originalFetch
  }
})
