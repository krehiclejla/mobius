/** A live assistant must keep its identity-owned slot while a steer cut catches up. */
import { test, expect } from '@playwright/test'
import { attachCleanup, createTaggedChat } from './_chatTracker.mjs'
import { mockDeliveryReady, runtimeSnapshot, testChatAgentSettings } from './_chatTestPrerequisites.mjs'
import { waitForChatShell } from './_chatSession.mjs'

const BASE = process.env.MOBIUS_URL || 'http://localhost:8001'
test.use({ serviceWorkers: 'block' })
attachCleanup()

test('a fetched steer never precedes its still-live sealed assistant, including before the cut event', async ({ page }) => {
  await mockDeliveryReady(page)
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await waitForChatShell(page)
  const chat = await createTaggedChat(page, 'steer-live-order')
  const oldId = 'fixture-assistant-before-steer'
  const nextId = 'fixture-assistant-after-steer'
  const text = 'The assistant paragraph already on screen.'
  const steer = { role: 'user', content: 'Keep this message below the paragraph.', ts: 3, cid: 'fixture-steer', steered: true }
  const items = [{ type: 'text', content: text, text_item_id: 'fixture-text' }]
  const messages = [
    { role: 'user', content: 'Original request', ts: 1, cid: 'fixture-request' },
    { role: 'assistant', id: oldId, content: text, blocks: [{ type: 'text', content: text }], ts: 2 },
    steer,
  ]
  // Characterize the allowed frontend handoff state, not a claim that every
  // backend detail response uses the old id: history already has A1/Q2 while
  // the still-live surface owns A1 until its ordered cut has been consumed.
  const runtime = runtimeSnapshot({ running: true, active_assistant_message_id: oldId, runtime_revision: 10000000, run_id: oldId, run_status: 'running' })
  await page.route(new RegExp(`/api/chats/${chat.id}\\?limit=`), route => route.fulfill({ json: {
    ...chat, ...runtime, ...testChatAgentSettings(), messages, total: messages.length, offset: 0,
  } }))
  await page.route(`**/api/chats/${chat.id}/runtime`, route => route.fulfill({ json: runtime }))
  // Keep transport open and drive the protocol boundary from the test;
  // no provider, arbitrary delivery delay, or agent run is needed.
  await page.addInitScript(({ chatId, oldId, items }) => {
    const nativeFetch = window.fetch.bind(window)
    window.fetch = (input, options) => {
      const url = typeof input === 'string' ? input : input.url
      if (!url.includes(`/api/chats/${chatId}/stream`)) return nativeFetch(input, options)
      const stream = new ReadableStream({ start(controller) {
        const emit = event => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`))
        window.__emitSteerOrderEvent = emit
        emit({ type: 'stream_snapshot', assistant_message_id: oldId, items })
        emit({ type: 'catch_up_done' })
      } })
      return Promise.resolve(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }))
    }
  }, { chatId: chat.id, oldId, items })
  await page.goto(`${BASE}/shell/?chat=${chat.id}`, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => typeof window.__emitSteerOrderEvent === 'function')
  const surface = page.locator('[data-chat-surface="painted"]')
  await expect(surface.locator('[data-active-assistant="true"]')).toContainText(text)
  const order = () => surface.locator('.chat__list .chat__msg').evaluateAll((rows, needles) => rows.map(row => row.textContent).filter(text => needles.some(needle => text.includes(needle))), ['Original request', text, steer.content])
  await expect.poll(order).toEqual([expect.stringContaining('Original request'), expect.stringContaining(text), expect.stringContaining(steer.content)])

  await page.evaluate(({ text, steer }) => {
    window.__steerOrderViolations = []
    const check = () => {
      const rows = [...document.querySelectorAll('[data-chat-surface="painted"] .chat__list .chat__msg')]
      const assistant = rows.findIndex(row => row.textContent.includes(text))
      const owner = rows.findIndex(row => row.textContent.includes(steer))
      if (owner >= 0 && assistant >= 0 && owner < assistant) window.__steerOrderViolations.push(rows.map(row => row.textContent))
    }
    window.__steerOrderObserver = new MutationObserver(check)
    window.__steerOrderObserver.observe(document.querySelector('[data-chat-surface="painted"] .chat__list'), { subtree: true, childList: true, characterData: true })
    check()
  }, { text, steer: steer.content })
  await page.evaluate(({ oldId, nextId, items, steer }) => window.__emitSteerOrderEvent({
    type: 'steered_into_turn', assistant_message_id: oldId, next_assistant_message_id: nextId,
    sealed_items: items, items: [], messages: [steer], ts: steer.ts, content: steer.content,
  }), { oldId, nextId, items, steer })
  await expect(surface.locator('[data-active-assistant="true"]')).toHaveCount(0)
  await expect(surface.locator('.chat__msg--assistant').filter({ hasText: text })).toHaveCount(1)
  await expect(surface.locator('.chat__msg--user').filter({ hasText: steer.content })).toHaveCount(1)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  expect(await page.evaluate(() => window.__steerOrderViolations)).toEqual([])
  await page.evaluate(() => window.__steerOrderObserver.disconnect())
})
