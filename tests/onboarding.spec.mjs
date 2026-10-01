import { test, expect } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { applyApp } from './app-source.mjs'

const BASE = process.env.MOBIUS_URL || 'http://localhost:8001'

// A disposable Store-compatible frame owns the same host navigation seam as
// the real Store. E2E disables first-boot apps, so discovery cannot assume a
// bootstrapped Store. Keep the catalog deterministic and exercise real shell
// app routing, intent delivery and reversible host history instead.
const STORE_FIXTURE = `
import React, { useEffect, useRef, useState } from 'react'

export default function StoreFixture() {
  const [detail, setDetail] = useState(null)
  const entry = useRef(null)
  useEffect(() => {
    function onIntent(event) {
      if (event.origin !== window.location.origin || event.source !== window.parent) return
      if (event.data?.type !== 'moebius:app-intent') return
      const match = /^app:([a-z0-9-]+)$/.exec(event.data.intent || '')
      if (!match || match[1] !== 'notes') return
      const handle = window.mobius.nav.open('app-store-detail', {
        onBack: () => { entry.current = null; setDetail(null) },
        onForward: () => { entry.current = handle; setDetail('Notes') },
      })
      entry.current = handle
      handle.outcome.then(({ status }) => {
        if (entry.current === handle && status === 'owned') setDetail('Notes')
      })
    }
    window.addEventListener('message', onIntent)
    return () => window.removeEventListener('message', onIntent)
  }, [])
  return detail ? <main><h1>{detail}</h1><details><summary>Privacy, access & technical details</summary><p>Privacy & access</p></details></main>
    : <main><h1>App Store</h1></main>
}
`

// Keep the browser's first-run requests inside this isolated test case.
test.use({ serviceWorkers: 'block' })

async function openGuide(page) {
  let completed = false
  let completions = 0

  await page.addInitScript(() => {
    if (sessionStorage.getItem('onboarding-test-started')) return
    localStorage.removeItem('mobius:walkthrough-completed')
    sessionStorage.setItem('onboarding-test-started', '1')
  })
  await page.route(/\/api\/owner\/walkthrough$/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ completed, completed_at: completed ? new Date().toISOString() : null }),
  }))
  await page.route(/\/api\/owner\/walkthrough\/complete$/, route => {
    completions += 1
    completed = true
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ completed: true, completed_at: new Date().toISOString() }),
    })
  })
  await page.route(/\/api\/auth\/providers\/status$/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: '{}',
  }))

  await page.goto(`${BASE}/shell/`)
  const guide = page.locator('aside.wt__card')
  await expect(guide).toBeVisible()
  return { guide, completionCount: () => completions }
}

test('a new owner can move through the guide and finish it', async ({ page }) => {
  const { guide, completionCount } = await openGuide(page)
  await expect(guide.getByRole('heading', { name: 'Welcome to Möbius' })).toBeVisible()
  await expect(guide).toHaveAttribute('role', 'region')
  await expect(guide).not.toHaveAttribute('aria-modal', 'true')

  await guide.getByRole('button', { name: 'Next' }).click()
  await expect(guide.getByRole('heading', { name: 'Connect an agent' })).toBeFocused()
  await expect(guide.getByRole('button', { name: 'OpenAI Codex' })).toBeVisible()
  // The shell stays keyboard reachable; the guide never traps focus.
  await expect(guide.getByRole('button', { name: 'Dismiss welcome' })).toBeVisible()

  await guide.getByRole('button', { name: 'Back' }).click()
  await expect(guide.getByRole('heading', { name: 'Welcome to Möbius' })).toBeVisible()

  for (const title of ['Connect an agent', 'Chat and Projects', 'Explore apps', 'Your settings', 'Choose a handle']) {
    await guide.getByRole('button', { name: 'Next' }).click()
    await expect(guide.getByRole('heading', { name: title })).toBeVisible()
  }

  await guide.getByRole('button', { name: 'Finish guide' }).click()
  await expect(guide).toHaveCount(0)
  await expect.poll(completionCount).toBe(1)

  const refreshedStatus = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/owner/walkthrough',
  )
  await page.reload()
  await refreshedStatus
  await expect(page.locator('.shell')).toBeVisible()
  await expect(guide).toHaveCount(0)
})

test('mobile guide content scrolls while its Next action remains usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 700 })
  const { guide } = await openGuide(page)
  for (let step = 0; step < 3; step += 1) {
    await guide.getByRole('button', { name: 'Next' }).click()
  }
  await expect(guide.getByRole('heading', { name: 'Explore apps' })).toBeVisible()

  await expect.poll(() => guide.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await guide.evaluate(element => { element.scrollTop = element.scrollHeight })
  await expect(guide.getByRole('button', { name: 'Next' })).toBeInViewport()

  await guide.getByRole('button', { name: 'Next' }).click()
  await expect(guide.getByRole('heading', { name: 'Your settings' })).toBeVisible()
})

test('short landscape keeps guide actions and dismissal reachable', async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 360 })
  const { guide, completionCount } = await openGuide(page)
  for (let step = 0; step < 3; step += 1) await guide.getByRole('button', { name: 'Next' }).click()
  await expect.poll(() => guide.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true)
  await expect(guide.getByRole('button', { name: 'Next' })).toBeInViewport()
  await expect(guide.getByRole('button', { name: 'Dismiss welcome' })).toBeInViewport()
  await guide.getByRole('button', { name: 'Dismiss welcome' }).click()
  await expect(guide).toHaveCount(0)
  await expect.poll(completionCount).toBe(1)
})

test('app discovery hands access review to Store and preserves the guide through history', async ({ page, request }) => {
  // The disposable E2E server skips default-app bootstrap. Install this test's
  // Store explicitly instead of making the test depend on host app inventory.
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  const token = await page.evaluate(() => localStorage.getItem('token'))
  const { app: storeApp } = await applyApp(request, token, {
    slug: `store-onboarding-${randomUUID()}`, name: 'App Store',
    jsxSource: STORE_FIXTURE,
  })
  try {
    await page.route(/\/api\/proxy\?/, route => {
      const source = new URL(route.request().url()).searchParams.get('url')
      if (source?.endsWith('/catalog.json')) return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ schema: 1, apps: [{
          id: 'notes', name: 'Notes', description: 'Write notes.',
          manifest_url: 'https://example.com/notes/mobius.json', raw_base: 'https://example.com/notes/',
        }] }),
      })
      return route.continue()
    })
    const { guide, completionCount } = await openGuide(page)
    const storeSelector = `iframe[data-app-id="${storeApp.id}"]`
    for (let step = 0; step < 3; step += 1) {
      await guide.getByRole('button', { name: 'Next', exact: true }).click()
    }
    const review = guide.getByRole('button', { name: 'Review Notes in App Store' })
    await expect(review).toBeEnabled()
    let installs = 0
    await page.route(/\/api\/apps\/install$/, route => {
      installs += 1
      return route.abort('blockedbyclient')
    })
    await review.click()
    await expect(guide).not.toBeVisible()
    await expect(page.locator(storeSelector)).toBeVisible()
    const store = page.frameLocator(storeSelector)
    await expect(store.getByRole('heading', { name: 'Notes', exact: true })).toBeVisible()
    await store.locator('summary').filter({ hasText: 'Privacy, access & technical details' }).click()
    await expect(store.getByText('Privacy & access', { exact: true })).toBeVisible()
    expect(installs).toBe(0)
    expect(completionCount()).toBe(0)
    await page.goBack() // Store detail -> Store browse.
    await page.goBack() // Store route -> previous workspace and guide.
    await expect(guide.getByRole('heading', { name: 'Explore apps' })).toBeVisible()
    await expect(guide.getByRole('heading', { name: 'Explore apps' })).toBeFocused()
    await page.goForward()
    await expect(guide).not.toBeVisible()
    await page.goBack()
    await expect(guide.getByRole('heading', { name: 'Explore apps' })).toBeFocused()
    expect(installs).toBe(0)
    expect(completionCount()).toBe(0)
  } finally {
    await request.delete(`${BASE}/api/apps/${storeApp.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  }
})

test('missing App Store does not block the guide or install from discovery', async ({ page }) => {
  await page.route(/\/api\/proxy\?/, route => {
    const source = new URL(route.request().url()).searchParams.get('url')
    if (source?.endsWith('/catalog.json')) return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ schema: 1, apps: [{ id: 'notes', name: 'Notes', description: 'Write notes.', manifest_url: 'https://example.com/notes/mobius.json', raw_base: 'https://example.com/notes/' }] }),
    })
    return route.continue()
  })
  await page.route(/\/api\/apps\/$/, async route => {
    const response = await route.fetch()
    const apps = (await response.json()).filter(app => app.slug !== 'store')
    await route.fulfill({ response, body: JSON.stringify(apps) })
  })
  const { guide, completionCount } = await openGuide(page)
  for (let step = 0; step < 3; step += 1) await guide.getByRole('button', { name: 'Next' }).click()
  await expect(guide.getByText('App Store is unavailable. You can continue the guide without installing anything.')).toBeVisible()
  await expect(guide.getByRole('button', { name: /^Review .+ in App Store$/ }).first()).toBeDisabled()
  await guide.getByRole('button', { name: 'Next' }).click()
  await expect(guide.getByRole('heading', { name: 'Your settings' })).toBeFocused()
  expect(completionCount()).toBe(0)
})

test('first-use guidance leaves the working shell interactive', async ({ page }) => {
  const { guide, completionCount } = await openGuide(page)
  await expect(guide).toHaveAttribute('role', 'region')
  await expect(guide).not.toHaveAttribute('aria-modal', 'true')
  await page.keyboard.press('Escape')
  await expect(guide).toBeVisible()
  expect(completionCount()).toBe(0)
  await guide.getByRole('button', { name: 'Dismiss welcome' }).click()
  await expect(guide).toHaveCount(0)
  await expect.poll(completionCount).toBe(1)
})
