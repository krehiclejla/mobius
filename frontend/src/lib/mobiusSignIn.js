// When this instance belongs to a mobius.you account, a signed-out visitor is
// sent straight to mobius.you to sign in instead of seeing a card that only
// holds a "Sign in with mobius.you" button. Three cases keep the card instead,
// so the redirect never traps anyone:
//   - an installed standalone app, whose storage is separate from the shell's:
//     the round trip would land the session in the wrong place;
//   - the owner signed out on purpose, so signing straight back in would
//     silently undo it, in every tab: that mark lives in localStorage, which
//     is shared by the browser, and the sign-in itself (a token written to the
//     same localStorage) is shared too;
//   - this tab already went to mobius.you and came back without signing in
//     (Back button, a failed round trip): that mark is per tab, in
//     sessionStorage, and the card is the way to retry.
// A completed sign-in clears both marks.

const SIGNED_OUT_KEY = 'mobius_signed_out'
const REDIRECTED_KEY = 'mobius_sign_in_redirected'
const PROBE_KEY = 'mobius_sign_in_probe'
// Set when a credential expires and cleared by the login card; an automatic
// redirect never shows the card, so a completed sign-in clears it here.
const EXPIRED_KEY = 'auth_expired'

function browserStore(name) {
  try {
    return globalThis[name] ?? null
  } catch {
    return null
  }
}

function browserStores() {
  return { local: browserStore('localStorage'), session: browserStore('sessionStorage') }
}

export function markExplicitSignOut(stores = browserStores()) {
  try { stores.local?.setItem(SIGNED_OUT_KEY, '1') } catch { /* private mode: card is the safe default */ }
  // An earlier expiry no longer describes why the card is showing.
  try { stores.session?.removeItem(EXPIRED_KEY) } catch { /* ignore */ }
}

/** Forget the marks once a sign-in has completed. */
export function clearSignInMarks(stores = browserStores()) {
  try { stores.local?.removeItem(SIGNED_OUT_KEY) } catch { /* ignore */ }
  try {
    stores.session?.removeItem(REDIRECTED_KEY)
    stores.session?.removeItem(EXPIRED_KEY)
  } catch { /* ignore */ }
}

// Readable is not enough: with a full or read-only store the marks would
// silently fail to persist, and a sign-out could be undone or a redirect could
// repeat. Only a store that takes a write counts as usable.
function storageAcceptsWrites(storage) {
  try {
    storage.setItem(PROBE_KEY, '1')
    const ok = storage.getItem(PROBE_KEY) === '1'
    storage.removeItem(PROBE_KEY)
    return ok
  } catch {
    return false
  }
}

export function shouldRedirectToMobiusSignIn({
  authMode,
  standalone = false,
  stores = browserStores(),
}) {
  if (authMode !== 'mobius' || standalone) return false
  // Without usable stores we cannot tell a loop from a first visit, so the
  // card stays: it is always safe and one tap from the same place.
  const { local, session } = stores
  if (!local || !session || !storageAcceptsWrites(local) || !storageAcceptsWrites(session)) return false
  try {
    return !local.getItem(SIGNED_OUT_KEY) && !session.getItem(REDIRECTED_KEY)
  } catch {
    return false
  }
}

/** Returns whether the mark was stored; navigate only when it was. */
export function recordSignInRedirect(stores = browserStores()) {
  try {
    stores.session.setItem(REDIRECTED_KEY, '1')
    return stores.session.getItem(REDIRECTED_KEY) === '1'
  } catch {
    return false
  }
}
