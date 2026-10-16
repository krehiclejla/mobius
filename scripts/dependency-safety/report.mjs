// The pull-request comment. Its first line is the marker the workflow uses to
// find and update its own comment. Lockfile and advisory text is untrusted, so
// it is shown as inline code that cannot break out or mention anyone.

export const REPORT_MARKER = '<!-- dependency-safety -->'
const MAX_LISTED = 50
const SEVERITIES = new Set(['info', 'low', 'moderate', 'high', 'critical'])
// Only a GitHub advisory page becomes a link; its URL has no room for Markdown.
const ADVISORY_URL = /^https:\/\/github\.com\/advisories\/GHSA-[\w-]+$/

const code = text => `\`${String(text).replace(/[`\p{Cc}\p{Cf}]/gu, '')}\``

export const hasAdvisories = advisories => Object.values(advisories ?? {}).some(list => list.length > 0)

export function packageNotes(change) {
  return [
    change.newPackage && 'new package',
    change.newInstallScript && 'runs an install script',
    change.changedDownload && 'same version, different download',
    change.outsideRegistry && 'not a standard npm registry download',
    change.misidentified && 'lockfile name or version differs from its download',
  ].filter(Boolean)
}

function truncated(lines) {
  const hidden = lines.length - MAX_LISTED
  return hidden > 0 ? [...lines.slice(0, MAX_LISTED), `- …and ${hidden} more`] : lines
}

function advisoryLine(name, advisory) {
  const title = code(advisory.title)
  const link = ADVISORY_URL.test(advisory.url) ? `[${title}](${advisory.url})` : title
  const severity = SEVERITIES.has(advisory.severity) ? advisory.severity : 'unknown severity'
  return `- ${code(name)}: ${link} (${severity}, affects ${code(advisory.vulnerable_versions)})`
}

function advisoryLines(advisories, advisoryError, packageCount) {
  if (advisoryError) return [`❌ **Could not check npm's advisory database** (${code(advisoryError)}). Re-run this check.`]
  if (!hasAdvisories(advisories)) return [`✅ No known malware or vulnerabilities in the ${packageCount} changed package(s).`]
  const found = Object.entries(advisories).flatMap(([name, list]) => list.map(advisory => advisoryLine(name, advisory)))
  return ['❌ **Known malware or vulnerabilities in changed packages. Do not merge until resolved.**', ...truncated(found)]
}

function flagLines(changes) {
  const flagged = changes
    .map(change => ({ id: `${change.name}@${change.version}`, notes: packageNotes(change) }))
    .filter(({ notes }) => notes.length > 0)
    .map(({ id, notes }) => `- ${code(id)}: ${notes.join(' · ')}`)
  if (flagged.length === 0) return ['Nothing else stood out.']
  return [`**Worth a look** (${flagged.length}):`, ...truncated(flagged)]
}

const FOOTER = [
  "<sub>Checks changed lockfile entries against npm's advisory database (including GitHub malware advisories)",
  'and flags new packages, install scripts and unexpected downloads. Updated on every push while the pull request',
  'still changes a lockfile; after a push that undoes those changes, it stays as it was.',
  "It reads only the lockfile, which a pull request's author can write freely, so it catches bad releases in",
  'honest npm and Dependabot updates and helps review, but it is no defense against a hostile author.</sub>',
].join(' ')

// `commit` is the full SHA of the checked head, which GitHub links.
const reportText = (commit, sections) =>
  [REPORT_MARKER, '### Dependency safety check', '', `Checked at ${commit}.`, '', ...sections, '', FOOTER, ''].join('\n')

export function renderReport({ commit, changes, advisories, advisoryError }) {
  return reportText(commit, [...advisoryLines(advisories, advisoryError, changes.length), '', ...flagLines(changes)])
}

export function renderUnreadableReport({ commit, files }) {
  return reportText(commit, [
    `❌ **Could not read ${files.length} changed lockfile(s), so no package in this pull request was checked.**`,
    'npm can still install from them: regenerate them with npm 7 or later (lockfile version 2 or 3).',
    ...truncated(files.map(file => `- ${code(file)}`)),
  ])
}
