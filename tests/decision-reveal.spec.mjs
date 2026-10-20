// Real React mounts: navigation ownership without a backend, credentials or live chats.
import { test, expect } from '@playwright/test'
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { fileURLToPath } from 'node:url'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

let server, base, cacheDir
const visible = { id: 'visible' }
test.beforeAll(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), 'decision-reveal-'))
  server = await createServer({
    root: fileURLToPath(new URL('../frontend', import.meta.url)),
    configFile: false, cacheDir, appType: 'custom', logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, hmr: false, ws: false },
  })
  server.middlewares.use(async (req, res, next) => {
    if (!['/', '/shell/'].includes(req.url.split('?')[0])) return next()
    res.setHeader('Content-Type', 'text/html')
    res.end(await server.transformIndexHtml(req.url, '<div id="root"></div><script type="module" src="/src/components/ChatView/hooks/__tests__/questionRevealFixture.jsx"></script>'))
  })
  await server.listen()
  base = `http://127.0.0.1:${server.httpServer.address().port}`
})
test.afterAll(async () => {
  await server?.close()
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true })
})
async function open(page, suffix = '/') {
  await page.goto(base + suffix)
  await page.waitForFunction(() => window.fixtureReady)
}
async function mount(page, views) {
  await page.evaluate(views => window.mountViews(views), views)
}
async function openDecision(page) {
  await page.evaluate(() => window.requestDecision('choice'))
  await mount(page, [visible])
  await expect(page.locator('#visible [data-question-id="choice"]')).toBeFocused()
  expect(await page.evaluate(() => window.pendingDecision())).toBeNull()
}

test('consumption preserves the initial activation but leave/return restores the reader', async ({ page }) => {
  await open(page)
  await openDecision(page)
  await mount(page, [{ ...visible, saved: 'saved:after-scroll' }])
  expect(await page.evaluate(() => window.activations)).toEqual([{ id: 'visible', anchor: 'question:choice' }])
  await mount(page, [{ ...visible, hidden: true, saved: 'saved:after-scroll' }])
  await expect(page.locator('#visible')).toBeHidden()
  await mount(page, [{ ...visible, saved: 'saved:after-scroll' }])
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'saved:after-scroll')
  expect(await page.evaluate(() => window.reveals.length)).toBe(1)
})

test('a newer same-chat search replaces the consumed decision activation', async ({ page }) => {
  await open(page)
  await openDecision(page)
  await mount(page, [{ ...visible, search: { id: 1, anchor: 'older-search-window' } }])
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'older-search-window')
  expect(await page.evaluate(() => window.activations.at(-1).anchor)).toBe('older-search-window')
})

test('cold exact-card navigation is seeded once, not on remount', async ({ page }) => {
  await open(page, '/shell/?chat=source&focus=question&question=choice')
  await mount(page, [visible])
  await expect(page.locator('#visible [data-question-id="choice"]')).toBeFocused()
  await page.evaluate(() => window.remountViews([{ id: 'visible', saved: 'saved:return' }]))
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'saved:return')
  expect(await page.evaluate(() => window.reveals.length)).toBe(1)
  expect(await page.evaluate(() => window.pendingDecision())).toBeNull()
})

test('hidden ready copy cannot consume the later-ready visible copy request', async ({ page }) => {
  await open(page)
  await page.evaluate(() => window.requestDecision('choice'))
  await mount(page, [{ id: 'hidden', hidden: true }, { ...visible, ready: false }])
  await expect(page.locator('#hidden [data-question-id="choice"]')).toHaveCount(1)
  expect(await page.evaluate(() => window.reveals)).toEqual([])
  expect(await page.evaluate(() => window.pendingDecision().questionId)).toBe('choice')
  await mount(page, [{ id: 'hidden', hidden: true }, visible])
  await expect(page.locator('#visible [data-question-id="choice"]')).toBeFocused()
  expect(await page.evaluate(() => window.reveals)).toEqual([{ id: 'visible', question: 'choice' }])
  expect(await page.evaluate(() => window.pendingDecision())).toBeNull()
})

test('missing exact card never redirects focus to another question', async ({ page }) => {
  await open(page)
  await page.evaluate(() => window.requestDecision('choice'))
  await mount(page, [{ ...visible, card: 'newer-question' }])
  await expect(page.locator('#visible [data-question-id]')).toHaveCount(1)
  expect(await page.evaluate(() => window.reveals)).toEqual([])
  expect(await page.evaluate(() => window.pendingDecision().questionId)).toBe('choice')
  await mount(page, [visible])
  await expect(page.locator('#visible [data-question-id="choice"]')).toBeFocused()
})

test('authoritative missing decision retires its intent without clearing a prior search', async ({ page }) => {
  await open(page)
  const search = await page.evaluate(() => window.requestSearch())
  await page.evaluate(() => window.requestDecision('choice'))
  await mount(page, [{ ...visible, ready: false }])
  expect(await page.evaluate(() => window.pendingDecision()?.questionId)).toBe('choice')
  await page.evaluate(() => window.reportMissingDecision())
  expect(await page.evaluate(() => window.pendingDecision())).toBeNull()
  expect(await page.evaluate(() => window.pendingSearch()?.id)).toBe(search.id)
})

test('new search retires an unconsumed card intent even while every view is hidden', async ({ page }) => {
  await open(page)
  await page.evaluate(() => window.requestDecision('choice'))
  await mount(page, [{ ...visible, hidden: true }])
  await expect(page.locator('#visible')).toBeHidden()
  const search = await page.evaluate(() => window.requestSearch())
  expect(await page.evaluate(() => window.pendingDecision())).toBeNull()
  await mount(page, [{ ...visible, search: { id: search.id, anchor: search.anchorKey } }])
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'older-search-window')
  expect(await page.evaluate(() => window.reveals)).toEqual([])
})

test('a fresh card request can supersede a previously consumed search activation', async ({ page }) => {
  await open(page)
  const view = { ...visible, search: { id: 9, anchor: 'older-search-window' } }
  await mount(page, [view])
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'older-search-window')
  await page.evaluate(() => window.requestDecision('choice'))
  await expect(page.locator('#visible [data-question-id="choice"]')).toBeFocused()
  await expect(page.locator('#visible')).toHaveAttribute('data-anchor', 'question:choice')
})
