import test from 'node:test'
import assert from 'node:assert/strict'

import { saveBlobAs, saveFileAs } from '../saveFile.js'

function fakeDocument() {
  const events = []
  const document = {
    events,
    body: { append(element) { events.push(['append', element.href]); element.attached = true } },
    createElement(tag) {
      assert.equal(tag, 'a')
      const element = {
        style: {},
        click() { events.push(['click', element.href, element.download, element.attached === true]) },
        remove() { events.push(['remove', element.href]); element.attached = false },
      }
      return element
    },
  }
  return document
}

async function withGlobals(overrides, fn) {
  const previous = Object.fromEntries(Object.keys(overrides).map(key => [key, globalThis[key]]))
  Object.assign(globalThis, overrides)
  try {
    return await fn()
  } finally {
    Object.assign(globalThis, previous)
  }
}

test('saving a file downloads it under the chosen name and leaves no link behind', async () => {
  const document = fakeDocument()
  await withGlobals({ document }, () => saveFileAs('/api/files/report.zip?token=t', 'report.zip'))
  assert.deepEqual(document.events, [
    ['append', '/api/files/report.zip?token=t'],
    ['click', '/api/files/report.zip?token=t', 'report.zip', true],
    ['remove', '/api/files/report.zip?token=t'],
  ])
})

test('saving in-memory content releases its object URL only after the download started', async () => {
  const document = fakeDocument()
  const revoked = []
  const timers = []
  const URL = {
    createObjectURL: () => 'blob:mobius/1',
    revokeObjectURL: url => revoked.push(url),
  }
  const setTimeout = (callback, delay) => { timers.push({ callback, delay }) }
  await withGlobals({ document, URL, setTimeout }, () => {
    saveBlobAs(new Blob(['x']), 'image.png')
    assert.deepEqual(document.events[1], ['click', 'blob:mobius/1', 'image.png', true])
    assert.deepEqual(revoked, [], 'revoking at once can cancel the download')
    assert.equal(timers.length, 1)
    assert.ok(timers[0].delay > 0)
    timers[0].callback()
  })
  assert.deepEqual(revoked, ['blob:mobius/1'])
})
