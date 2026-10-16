import test from 'node:test'
import assert from 'node:assert/strict'
import {
  clearSignInMarks,
  markExplicitSignOut,
  recordSignInRedirect,
  shouldRedirectToMobiusSignIn,
} from '../mobiusSignIn.js'

function memoryStores() {
  return { local: memoryStorage(), session: memoryStorage() }
}

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)) },
    removeItem: (key) => { data.delete(key) },
  }
}

test('redirects a signed-out visitor of a mobius.you instance on a fresh tab', () => {
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: memoryStores() }), true)
})

test('never redirects a self-hosted instance that signs in with a password', () => {
  for (const authMode of ['local', undefined, null, '']) {
    assert.equal(shouldRedirectToMobiusSignIn({ authMode, stores: memoryStores() }), false, String(authMode))
  }
})

test('an explicit sign-out keeps the card so the owner is not signed straight back in', () => {
  const stores = memoryStores()
  markExplicitSignOut(stores)
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores }), false)
})

test('coming back from mobius.you without signing in shows the card instead of looping', () => {
  const stores = memoryStores()
  recordSignInRedirect(stores)
  // However long the visitor stayed away, only a completed sign-in clears it.
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores }), false)
})

test('an installed standalone app keeps the card', () => {
  assert.equal(
    shouldRedirectToMobiusSignIn({ authMode: 'mobius', standalone: true, stores: memoryStores() }),
    false,
  )
})

test('completing a sign-in clears both marks', () => {
  const stores = memoryStores()
  markExplicitSignOut(stores)
  recordSignInRedirect(stores)
  clearSignInMarks(stores)
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores }), true)
})

test('unreadable storage keeps the card, the always-safe choice', () => {
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: { local: null, session: null } }), false)
  const throwing = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } }
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: { local: throwing, session: throwing } }), false)
  assert.doesNotThrow(() => markExplicitSignOut({ local: throwing, session: throwing }))
  assert.doesNotThrow(() => recordSignInRedirect({ local: throwing, session: throwing }))
})

test('storage that reads but rejects writes keeps the card, so a sign-out cannot be undone', () => {
  const full = {
    getItem: () => null,
    setItem() { throw new DOMException('quota', 'QuotaExceededError') },
    removeItem() {},
  }
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: { local: full, session: full } }), false)
  assert.equal(recordSignInRedirect({ local: full, session: full }), false)
})

test('a successful redirect mark reports that it was stored', () => {
  assert.equal(recordSignInRedirect(memoryStores()), true)
})

test('a sign-out holds in every tab, a redirect mark only in its own', () => {
  const local = memoryStorage()
  const firstTab = { local, session: memoryStorage() }
  const newTab = { local, session: memoryStorage() }
  markExplicitSignOut(firstTab)
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: newTab }), false)

  clearSignInMarks(newTab)
  recordSignInRedirect(firstTab)
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: firstTab }), false)
  assert.equal(shouldRedirectToMobiusSignIn({ authMode: 'mobius', stores: newTab }), true)
})

test('a completed sign-in clears a stale session-expired flag the redirect never showed', () => {
  const stores = memoryStores()
  stores.session.setItem('auth_expired', '1')
  clearSignInMarks(stores)
  assert.equal(stores.session.getItem('auth_expired'), null)
})

test('an explicit sign-out drops an older session-expired flag so the card does not claim expiry', () => {
  const stores = memoryStores()
  stores.session.setItem('auth_expired', '1')
  markExplicitSignOut(stores)
  assert.equal(stores.session.getItem('auth_expired'), null)
})
