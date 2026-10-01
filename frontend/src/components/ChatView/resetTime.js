// Pause timestamps are explicit-UTC ISO strings. Render each as the viewer's
// LOCAL clock, by day, so a bare time cannot hide a day boundary.
//
// The label carries its own preposition so a caller can splice it after
// "Resets" / "resets" and read naturally in every bucket. It always names the
// day so a bare clock time can never read ambiguously across a boundary:
//   same-day  → "today at 13:40"
//   tomorrow  → "tomorrow at 13:40"
//   further/past → "11th Sep 2026, 13:40"
// Returns null on a missing / unparseable value so the card degrades to just
// the message rather than showing a garbage label.
import { formatDateTime, formatTime } from '../../lib/dateTimeFormat.js'

// Backend provider parks distinguish usage and request-rate limits. `limit`
// remains accepted for older persisted cards and pre-contract stream payloads.
export function isProviderLimitPause(pause) {
  return ['usage_limit', 'rate_limit', 'limit'].includes(pause?.kind)
}

// Old parks stored a clamped retry deadline in resets_at. Only a pause with
// the new check_at contract can attest that resets_at is a provider reset.
export function pauseTiming(pause) {
  const valid = value => value && !Number.isNaN(new Date(value).getTime()) ? value : null
  const explicitCheck = valid(pause?.check_at)
  return {
    checkAt: explicitCheck || (!pause?.check_at ? valid(pause?.resets_at) : null),
    resetAt: explicitCheck ? valid(pause?.resets_at) : null,
  }
}

export function formatResetTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  const time = formatTime(d)
  // Compare local calendar days, not the raw 24h delta — a reset seven hours
  // from now can still be "tomorrow" if it crosses local midnight.
  const startOfDay = (x) =>
    new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const dayDelta = Math.round(
    (startOfDay(d) - startOfDay(new Date())) / 86400000,
  )
  if (dayDelta < 0) return formatDateTime(d)
  if (dayDelta === 0) return `today at ${time}`
  if (dayDelta === 1) return `tomorrow at ${time}`
  return formatDateTime(d)
}
