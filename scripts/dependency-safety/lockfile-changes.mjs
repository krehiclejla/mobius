// Which package installs a pull request adds or alters in an npm lockfile
// (v2/v3). Every entry is compared on its own, so a tampered copy cannot hide
// behind an unchanged duplicate of the same version elsewhere in the tree.

import { registryTarball } from './registry.mjs'

const NODE_MODULES = 'node_modules/'

// npm 7+ installs from `packages` (lockfile v2/v3). npm still installs a v1
// lockfile from its `dependencies` tree, which this checker doesn't read, so a
// lockfile in any other shape must fail the check rather than pass unread.
export const isCheckableLockfile = lock =>
  [2, 3].includes(lock?.lockfileVersion) && typeof lock.packages === 'object' && lock.packages !== null

// What the lockfile says: aliased installs record the real package name,
// others are named by their path.
function claimedIdentity(installPath, entry) {
  const name = entry.name ?? installPath.slice(installPath.lastIndexOf(NODE_MODULES) + NODE_MODULES.length)
  return { name, version: entry.version ?? 'unknown' }
}

// A pull request can write any name and version into its lockfile, so a
// registry install is identified by the tarball npm actually downloads.
function install([installPath, entry]) {
  const claimed = claimedIdentity(installPath, entry)
  const tarball = entry.resolved ? registryTarball(entry.resolved) : null
  const identity = tarball ?? claimed
  return {
    ...entry,
    ...identity,
    claimed: `${claimed.name}@${claimed.version}`,
    outsideRegistry: Boolean(entry.resolved) && !tarball,
    misidentified: identity.name !== claimed.name || identity.version !== claimed.version,
  }
}

// npm installs every non-link entry that has a version or a download URL.
function installs(lock) {
  return Object.entries(lock?.packages ?? {})
    .filter(([installPath, entry]) => installPath && !entry.link && (entry.version || entry.resolved))
    .map(install)
}

// Everything that decides which code npm downloads and whether it runs on
// install, plus what the lockfile calls it, so a changed claim is reported too.
const fingerprint = entry =>
  [entry.name, entry.version, entry.claimed, entry.integrity, entry.resolved, Boolean(entry.hasInstallScript)].join(' ')
const download = entry => `${entry.integrity} ${entry.resolved}`

function describeChange(entry, previousVersions) {
  const sameVersion = previousVersions.filter(old => old.version === entry.version)
  return {
    name: entry.name,
    version: entry.version,
    newPackage: previousVersions.length === 0,
    changedDownload: sameVersion.length > 0 && !sameVersion.some(old => download(old) === download(entry)),
    newInstallScript: Boolean(entry.hasInstallScript) && !previousVersions.some(old => old.hasInstallScript),
    outsideRegistry: entry.outsideRegistry,
    misidentified: entry.misidentified,
  }
}

export function changedPackages(baseLock, headLock) {
  const base = installs(baseLock)
  const known = new Set(base.map(fingerprint))
  const changed = new Map(installs(headLock)
    .filter(entry => !known.has(fingerprint(entry)))
    .map(entry => [fingerprint(entry), entry]))
  return [...changed.values()].map(entry => describeChange(entry, base.filter(old => old.name === entry.name)))
}
