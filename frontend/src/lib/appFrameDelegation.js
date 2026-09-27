// Browser features the shell delegates to a mini-app frame.
//
// The app document is response-sandboxed to an opaque origin. A bare
// `allow="feature"` delegates only to the iframe's `src` origin, which an
// opaque document never matches, so every feature must name `*` to reach it.
// An explicit `fullscreen` entry also overrides the element's
// `allowfullscreen` attribute, so a bare one silently disables fullscreen.
// `*` reaches only this frame and what it nests; each nested frame still
// needs its own grant.
//
// `clipboard-write` stays bare on purpose: apps copy through the host clipboard
// broker (`window.mobius.clipboard`), not the raw browser API.
export const APP_FRAME_ALLOW = 'clipboard-write; fullscreen *; gamepad *'
