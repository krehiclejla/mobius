import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  HISTORY_PREFETCH_VIEWPORTS,
  olderHistoryRetryDelayMs,
  olderHistoryShouldLoad,
  paginationViewportCompensationAllowed,
} from '../scroll/policy.js'

function scrollEl({ scrollHeight, scrollTop, clientHeight }) {
  return { scrollHeight, scrollTop, clientHeight }
}

test('older history prefetches several viewports ahead and fills a short page', () => {
  assert.equal(olderHistoryShouldLoad(scrollEl({
    scrollHeight: 8000,
    scrollTop: 800 * HISTORY_PREFETCH_VIEWPORTS,
    clientHeight: 800,
  }), { userDriven: true }), true)
  assert.equal(olderHistoryShouldLoad(scrollEl({
    scrollHeight: 8000,
    scrollTop: 800 * HISTORY_PREFETCH_VIEWPORTS + 1,
    clientHeight: 800,
  }), { userDriven: true }), false)
  assert.equal(olderHistoryShouldLoad(scrollEl({
    scrollHeight: 800, scrollTop: 0, clientHeight: 800,
  })), true)
  assert.equal(olderHistoryShouldLoad(scrollEl({
    scrollHeight: 2000, scrollTop: 0, clientHeight: 800,
  })), false)
})

test('pagination compensation preserves the same reader generation under touch', () => {
  assert.equal(paginationViewportCompensationAllowed({
    capturedVersion: 7,
    currentVersion: 7,
  }), true)
  assert.equal(paginationViewportCompensationAllowed({
    capturedVersion: 7,
    currentVersion: 8,
  }), false)
})

test('failed pagination retries quietly with a capped backoff instead of a retry control', () => {
  assert.equal(olderHistoryRetryDelayMs(0), 1000)
  assert.ok(olderHistoryRetryDelayMs(1) > olderHistoryRetryDelayMs(0))
  assert.equal(olderHistoryRetryDelayMs(4), 30000)
  assert.equal(olderHistoryRetryDelayMs(50), 30000,
    'a reader waiting at the top keeps getting a steady, bounded retry cadence')
})
