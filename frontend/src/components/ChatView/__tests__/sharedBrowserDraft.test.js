/* Shared draft text is tab/grant scoped and never touches owner durable drafts. */
import test from 'node:test'
import assert from 'node:assert/strict'
import 'fake-indexeddb/auto'
import { createStore, get, set } from 'idb-keyval'
import { setActiveSharedBrowserGrantId } from '../../../lib/sharedBrowserWorkspace.js'

function storageStub() {
  const values = new Map()
  return {
    get length() { return values.size },
    key: index => [...values.keys()][index] ?? null,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  }
}

test('guest composer reload uses its own tab storage, not owner IndexedDB', async () => {
  globalThis.location = { pathname: '/shell/shared' }
  globalThis.sessionStorage = storageStub()
  setActiveSharedBrowserGrantId('grant-A')
  const ownerStore = createStore('mobius-owner-drafts', 'drafts-v1')
  await set('chat-1', 'owner draft', ownerStore)
  const drafts = await import('../composerDraft.js')
  drafts.persistComposerDraft('chat-1', 'guest draft')
  drafts._clearComposerDraftMemoryForTests()
  assert.equal((await drafts.readComposerDraftAsync('chat-1')).input, 'guest draft')
  assert.equal(await get('chat-1', ownerStore), 'owner draft')
  setActiveSharedBrowserGrantId('grant-B')
  drafts._clearComposerDraftMemoryForTests()
  assert.equal((await drafts.readComposerDraftAsync('chat-1')).input, '')
  assert.equal(await get('chat-1', ownerStore), 'owner draft')
})

test('same-chat guest A to B to A restores only each grant draft', async () => {
  globalThis.location = { pathname: '/shell/shared' }
  globalThis.sessionStorage = storageStub()
  const drafts = await import('../composerDraft.js')
  setActiveSharedBrowserGrantId('grant-A')
  drafts.persistComposerDraft('same-chat', 'unsent A')
  assert.equal(drafts.readComposerDraft('same-chat').input, 'unsent A')
  setActiveSharedBrowserGrantId('grant-B')
  assert.equal(drafts.readComposerDraft('same-chat').input, '')
  drafts.persistComposerDraft('same-chat', 'unsent B')
  setActiveSharedBrowserGrantId('grant-A')
  assert.equal(drafts.readComposerDraft('same-chat').input, 'unsent A')
  setActiveSharedBrowserGrantId(null)
  assert.equal(drafts.readComposerDraft('same-chat').input, '')
})
import { disclosureIsOpen, persistDisclosureOpen, _resetDisclosureStateForTests } from '../disclosureState.js'
import { readingPositionFor, writeReadingPosition } from '../scroll/readingPositions.js'

test('disclosure and reading position partition same chat A to B to A', () => {
  globalThis.location = { pathname: '/shell/shared' }
  globalThis.sessionStorage = storageStub()
  _resetDisclosureStateForTests()
  setActiveSharedBrowserGrantId('A')
  persistDisclosureOpen('chat', 'tool', true)
  writeReadingPosition('chat', { kind: 'ANCHOR_AT', key: 'A-row' })
  setActiveSharedBrowserGrantId('B')
  assert.equal(disclosureIsOpen('chat', 'tool'), false)
  assert.equal(readingPositionFor('chat'), null)
  persistDisclosureOpen('chat', 'other', true)
  writeReadingPosition('chat', { kind: 'ANCHOR_AT', key: 'B-row' })
  setActiveSharedBrowserGrantId('A')
  assert.equal(disclosureIsOpen('chat', 'tool'), true)
  assert.equal(disclosureIsOpen('chat', 'other'), false)
  assert.equal(readingPositionFor('chat')?.key, 'A-row')
  setActiveSharedBrowserGrantId(null)
  assert.equal(disclosureIsOpen('chat', 'tool'), false)
  assert.equal(readingPositionFor('chat'), null)
  persistDisclosureOpen('chat', 'preauth', true)
  writeReadingPosition('chat', { kind: 'ANCHOR_AT', key: 'preauth' })
  setActiveSharedBrowserGrantId('A')
  assert.equal(disclosureIsOpen('chat', 'preauth'), false)
  assert.equal(readingPositionFor('chat')?.key, 'A-row')
})
