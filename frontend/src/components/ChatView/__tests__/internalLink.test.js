import test from 'node:test'
import assert from 'node:assert/strict'

import { isPlainLeftClick, openInternalLink } from '../internalLink.js'

function clickEvent(extra = {}) {
  const event = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, prevented: false, ...extra }
  event.preventDefault = () => { event.prevented = true }
  return event
}

function withWindowLocation(href, fn) {
  const previous = globalThis.window
  const origin = URL.canParse(href) ? new URL(href).origin : 'null'
  globalThis.window = { location: { href, origin } }
  try {
    return fn()
  } finally {
    globalThis.window = previous
  }
}

const MODIFIED_OR_SECONDARY_CLICKS = [
  { metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { button: 2 },
]

test('only an unmodified primary click counts as a plain left click', () => {
  assert.equal(isPlainLeftClick(clickEvent()), true)
  for (const extra of MODIFIED_OR_SECONDARY_CLICKS) {
    assert.equal(isPlainLeftClick(clickEvent(extra)), false, JSON.stringify(extra))
  }
})

test('a plain click on an internal link opens it in the workspace instead of a new tab', () => {
  const opened = []
  const event = clickEvent()
  const handled = withWindowLocation('https://mobius.test/shell?chat=current', () => (
    openInternalLink(event, '/shell?app=42', url => opened.push(url.href))
  ))
  assert.equal(handled, true)
  assert.equal(event.prevented, true)
  assert.deepEqual(opened, ['https://mobius.test/shell?app=42'])
})

test('a plain click on a link to another origin is never taken over, even with shell parameters', () => {
  const OTHER_ORIGINS = [
    'https://elsewhere.example/shell?app=5&intent=open',
    '//elsewhere.example/?chat=abc',
    'http://mobius.test/shell?app=5',
  ]
  for (const href of OTHER_ORIGINS) {
    const event = clickEvent()
    const handled = withWindowLocation('https://mobius.test/shell?chat=current', () => (
      openInternalLink(event, href, () => {
        throw new Error('a link on another origin must not navigate in the workspace')
      })
    ))
    assert.equal(handled, false, href)
    assert.equal(event.prevented, false, href)
  }
})

test('modified and secondary clicks keep the browser default for an internal link', () => {
  for (const extra of MODIFIED_OR_SECONDARY_CLICKS) {
    const event = clickEvent(extra)
    const handled = openInternalLink(event, '/shell?app=42', () => {
      throw new Error('a modified click must not navigate in the workspace')
    })
    assert.equal(handled, false)
    assert.equal(event.prevented, false, JSON.stringify(extra))
  }
})

test('without a workspace navigator or a usable href the link is followed normally', () => {
  const noNavigator = clickEvent()
  assert.equal(openInternalLink(noNavigator, '/shell?app=42', null), false)
  assert.equal(noNavigator.prevented, false)

  const noHref = clickEvent()
  assert.equal(openInternalLink(noHref, '', () => {}), false)
  assert.equal(noHref.prevented, false)

  const unparseable = clickEvent()
  const handled = withWindowLocation('not a base url', () => (
    openInternalLink(unparseable, '/shell?app=42', () => {})
  ))
  assert.equal(handled, false)
  assert.equal(unparseable.prevented, false)
})
