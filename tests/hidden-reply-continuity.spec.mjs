/** Hidden steer cuts keep one paragraph and one lazy source footer on every provider path. */
import { test } from '@playwright/test'
import { attachCleanup, createTaggedChat } from './_chatTracker.mjs'
import { checkHiddenReplyContinuity } from './_replyContinuityFixture.mjs'

const BASE = process.env.MOBIUS_URL || 'http://localhost:8001'
test.use({ serviceWorkers: 'block' })
attachCleanup()

for (const provider of ['claude', 'codex', 'mobius', 'responses']) {
  test(`${provider}: hidden replay stays continuous through live, saved and partial source reads`, async ({ page }) => {
    await page.goto(BASE, { waitUntil: 'domcontentloaded' })
    const chat = await createTaggedChat(page, `hidden-reply-${provider}`)
    await checkHiddenReplyContinuity(page, chat, BASE, provider)
  })
}
