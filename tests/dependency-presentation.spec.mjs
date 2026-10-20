// Real production panels, mounted without a backend, login or live owner data.
import { test, expect } from '@playwright/test'
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js'
import { fileURLToPath } from 'node:url'
import tailwindcss from '../frontend/node_modules/@tailwindcss/vite/dist/index.mjs'
import { mkdtemp, rm, mkdir, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
test.use({ storageState: { cookies: [], origins: [] } })
let server, base, cacheDir
test.beforeAll(async () => {
  const themeSource = await readFile(new URL('../backend/app/theme.py', import.meta.url), 'utf8')
  const theme = themeSource.split('DEFAULT_THEME = ')[1].split('\"\"\"')[1].replace(/^\\\n/, '')
  cacheDir = await mkdtemp(join(tmpdir(), 'dependency-panels-'))
  server = await createServer({ root: fileURLToPath(new URL('../frontend', import.meta.url)),
    configFile: false, cacheDir, optimizeDeps: { entries: ['src/components/ChatView/__tests__/dependencyPresentationFixture.jsx'] }, plugins: [tailwindcss()], appType: 'custom', logLevel: 'error', esbuild: { jsx: 'automatic' },
    server: { host: '127.0.0.1', port: 0, hmr: false, ws: false } })
  server.middlewares.use(async (req, res, next) => {
    if (req.url !== '/') return next()
    res.setHeader('Content-Type', 'text/html')
    res.end(await server.transformIndexHtml(req.url, `<link rel="stylesheet" href="/vendor/fonts/mobius-fonts.css"><style>${theme}</style><meta name="viewport" content="width=device-width,initial-scale=1"><div id="root"></div><script type="module" src="/src/components/ChatView/__tests__/dependencyPresentationFixture.jsx"></script>`))
  })
  await server.listen()
  base = `http://127.0.0.1:${server.httpServer.address().port}`
})
test.afterAll(async () => { await server?.close(); if (cacheDir) await rm(cacheDir, { recursive: true, force: true }) })
async function open(page) {
  page.on('pageerror', error => console.error('Fixture error:', error.message))
  await page.goto(base)
  await expect(page.getByRole('heading', { name: 'Goal handoffs' })).toBeVisible()
  await page.evaluate(() => document.fonts.ready)
  await page.getByRole('button', { name: 'Expand: Waiting · Finish the blocked update' }).click()
}

test('card backlink disclosures are keyboard accessible and navigate without answering', async ({ page }) => {
  await open(page)
  const related = page.locator('.qcard__related-work > summary')
  await related.focus(); await page.keyboard.press('Enter')
  const many = page.locator('.chat__dependency-backlinks > summary')
  await many.focus(); await page.keyboard.press('Enter')
  const link = page.getByRole('link', { name: 'Finish the blocked update' })
  await expect(link).toBeVisible()
  await expect(link).toHaveCSS('text-decoration-line', 'underline')
  await link.click()
  expect(await page.evaluate(() => window.navigations)).toEqual([base + '/shell/?chat=dependent'])
  await expect(page.locator('.qcard')).not.toHaveClass(/qcard--answered/)
})

test('exact decision link is distinct from source backlinks and preserves full condition', async ({ page }) => {
  await open(page)
  const details = page.getByRole('region', { name: 'Dependent Goal' })
  await expect(details).toContainText('after preserving local work and checking the host')
  const link = details.getByRole('link', { name: /^Open decision/ })
  await expect(link).toHaveCSS('text-decoration-line', 'underline')
  await link.focus(); await page.keyboard.press('Tab'); await page.keyboard.press('Shift+Tab')
  await expect(link).toBeFocused()
  await expect(link).toHaveCSS('outline-style', 'solid')
  await page.keyboard.press('Enter')
  expect(await page.evaluate(() => window.navigations.at(-1))).toBe(base + '/shell/?chat=source&focus=question&question=choice')
  await page.evaluate(() => window.mountFixture({ held: true }))
  await expect(details).not.toContainText('Continues automatically')
  await expect(details).toContainText('Saved until you continue this Goal')
})

test('recovery action remains actionable and saved outcome expands as a quiet clock record', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'Resume', exact: true }).click()
  expect(await page.evaluate(() => window.resumes)).toBe(1)
  const history = page.getByTestId('receipt').locator('.chat__wait-history')
  await history.locator('summary').click()
  await expect(history).toContainText('You chose Not now')
  await expect(history.getByRole('link', { name: /^Open decision/ })).toBeVisible()
})

test('owner-input diamond is painted in the existing slot even in an inline legend', async ({ page }) => {
  await open(page)
  const diamond = page.getByRole('img', { name: 'Your input is needed' })
  const bounds = await diamond.boundingBox()
  expect(bounds.width).toBeGreaterThan(5)
  expect(bounds.height).toBeGreaterThan(5)
  await expect(diamond).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
  const rgb = await diamond.evaluate(el => getComputedStyle(el).backgroundColor)
  expect(rgb).not.toBe('rgb(139, 108, 247)')
})

for (const width of [390, 960]) test(`short and long receipts fill the real assistant column at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1100 })
  await open(page)
  for (const wrapped of [false, true]) for (const condition of ['Release choice', 'Choose the exact release to install after preserving local work and checking the host']) {
    await page.evaluate(props => window.mountFixture(props), { wrapped, receiptCondition: condition, comparisons: true })
    const message = page.getByTestId('receipt')
    const card = message.locator('.chat__wait-history')
    const details = card.locator('details')
    await details.evaluate(el => { el.open = false })
    for (const expanded of [false, true]) {
      if (expanded) await card.locator('summary').click()
      const column = await message.boundingBox()
      const box = await card.boundingBox()
      expect(Math.round(box.width)).toBe(Math.round(column.width))
      expect((await card.innerText()).split(condition).length - 1).toBe(1)
      expect((await card.innerText()).split('You chose Not now').length - 1).toBe(1)
      await expect(card.locator('.chat__goal-history-objective')).toHaveCSS('font-size', '13px')
      await expect(card.locator('.chat__goal-task-title')).toHaveCount(0)
      if (expanded) {
        const link = card.getByRole('link', { name: /^Open decision/ })
        await expect(link).toHaveCSS('text-decoration-line', 'underline')
        await expect(link).toHaveCSS('font-size', '12px')
        await link.focus(); await page.keyboard.press('Enter')
        expect(await page.evaluate(() => window.navigations.at(-1))).toBe(base + '/shell/?chat=source&focus=question&question=choice')
      }
    }
    const siblings = page.getByTestId('siblings')
    const column = await siblings.boundingBox()
    for (const sibling of await siblings.locator('.chat__goal-history').all()) {
      expect(Math.round((await sibling.boundingBox()).width)).toBe(Math.round(column.width))
    }
  }
  // Exercise the same visible action for claimed work, not just decisions.
  await page.evaluate(() => window.mountFixture({ kind: 'claim_acquisition', comparisons: true }))
  const workLink = page.getByRole('region', { name: 'Dependent Goal' }).getByRole('link', { name: /^Open chat/ })
  await expect(workLink).toHaveCSS('text-decoration-line', 'underline')
  await workLink.focus(); await page.keyboard.press('Enter')
  expect(await page.evaluate(() => window.navigations.at(-1))).toBe(base + '/shell/?chat=source')
  await page.evaluate(() => window.mountFixture({ comparisons: true }))
  await page.locator('.qcard__related-work > summary').click()
  await page.locator('.chat__dependency-backlinks > summary').click()
  const receiptDetails = page.getByTestId('receipt').locator('details')
  if (!await receiptDetails.evaluate(el => el.open)) await receiptDetails.locator('summary').click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  if (process.env.MOBIUS_PRESENTATION_SCREENS) {
    await mkdir(process.env.MOBIUS_PRESENTATION_SCREENS, { recursive: true })
    // Shell overflow containment is real CSS; give this fixture's full content
    // enough viewport height rather than clipping the last recovery action.
    const height = await page.locator('main').evaluate(el => Math.ceil(el.scrollHeight + el.offsetTop + 24))
    await page.setViewportSize({ width, height: Math.max(1100, height) })
    await page.screenshot({ path: join(process.env.MOBIUS_PRESENTATION_SCREENS, `final-${width}.png`), fullPage: true })
  }
})


test('stop following confirms, cancels and retries without touching the source card', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await open(page)
  const details = page.getByRole('region', { name: 'Dependent Goal' })
  const stop = details.getByRole('button', { name: /^Stop following:/ })
  await stop.dblclick()
  expect(await page.evaluate(() => window.withdrawals)).toEqual([])
  await expect(details.getByRole('button', { name: /^Confirm stop following:/ })).toBeVisible()
  await details.getByRole('button', { name: 'Keep following' }).click()
  await stop.focus(); await page.keyboard.press('Enter')
  await expect(details).toContainText('Your Goal stays unfinished')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(await page.evaluate(() => window.withdrawals)).toEqual([])
  await details.getByRole('button', { name: 'Keep following' }).click()
  await expect(stop).toBeVisible()
  await stop.click()
  await page.evaluate(() => { window.failWithdrawal = true })
  const confirm = details.getByRole('button', { name: /^Confirm stop following:/ })
  await confirm.focus(); await page.keyboard.press('Enter')
  await expect(details.getByRole('alert')).toHaveText('Could not stop following. Try again.')
  await expect(confirm).toBeEnabled()
  await page.evaluate(() => { window.failWithdrawal = false })
  await confirm.click()
  await expect(details).toContainText('You stopped following this')
  await expect(details.getByRole('button', { name: /following/ })).toHaveCount(0)
  await expect(details).not.toContainText('Continues automatically')
  expect(await page.evaluate(() => window.withdrawals)).toEqual(['d', 'd'])
  await expect(page.locator('.qcard')).not.toHaveClass(/qcard--answered/)
  await expect(details.getByRole('link', { name: /^Open decision/ })).toBeVisible()
})

test('saved message history links use internal navigation in goal and wait cards', async ({ page }) => {
  await open(page)
  await page.evaluate(() => window.mountFixture({ comparisons: true }))
  const history = page.getByTestId('message-history')
  const cards = history.locator('.chat__goal-history')
  await expect(cards).toHaveCount(3)
  for (const card of await cards.all()) {
    await card.locator('summary').click()
    await card.getByRole('link', { name: /^Open decision/ }).click()
  }
  expect(await page.evaluate(() => window.navigations)).toEqual(Array(3).fill(base + '/shell/?chat=source&focus=question&question=choice'))
})
