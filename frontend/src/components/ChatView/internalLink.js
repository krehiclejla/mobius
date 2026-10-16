/* Opening an in-shell link from chat content without leaving the workspace. */

// A modified or non-primary click keeps the browser's own behaviour (new tab,
// new window, save link), so only a plain left click is ever taken over.
export function isPlainLeftClick(event) {
  return event.button === 0
    && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}

/**
 * Hands a plain left click on an internal link to the shell's navigator.
 * Anything else follows the link normally: a modified click, an href that
 * cannot be parsed, or one on another origin.
 *
 * @returns {boolean} true when the click was taken over
 */
export function openInternalLink(event, href, onInternalNav) {
  if (!onInternalNav || !href || !isPlainLeftClick(event)) return false
  let url
  try {
    url = new URL(href, window.location.href)
  } catch {
    return false
  }
  // The navigator reads ?app=, ?intent= and ?chat= from any URL, so a link to
  // another site must never be taken over, whoever authored it.
  if (url.origin !== window.location.origin) return false
  event.preventDefault()
  onInternalNav(url)
  return true
}
