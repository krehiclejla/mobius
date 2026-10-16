#!/usr/bin/env node
// Compares the npm lockfiles of two commits and writes the safety report. It
// only reads lockfile text from Git, so untrusted commits are safe inputs.
// Exits 1 when a changed lockfile can't be read, a changed package has a known
// advisory, or the lookup failed; writes no report when no lockfile entry changed.

import { execFileSync, spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { posix } from 'node:path'
import { parseArgs } from 'node:util'

import { changedPackages, isCheckableLockfile } from './lockfile-changes.mjs'
import { findAdvisories } from './registry.mjs'
import { hasAdvisories, renderReport, renderUnreadableReport } from './report.mjs'

const git = (...args) => execFileSync('git', args, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 })

function readLock(ref, file) {
  const exists = spawnSync('git', ['cat-file', '-e', `${ref}:${file}`]).status === 0
  return exists ? JSON.parse(git('show', `${ref}:${file}`)) : null
}

// -z keeps paths verbatim: otherwise git quotes non-ASCII paths and the
// lockfile name would never match.
function changedLockfiles(baseRef, headRef) {
  return git('diff', '--name-only', '-z', baseRef, headRef)
    .split('\0')
    .filter(file => posix.basename(file) === 'package-lock.json')
    .map(file => ({ file, base: readLock(baseRef, file), head: readLock(headRef, file) }))
}

async function lookUpAdvisories(changes) {
  try {
    return { advisories: await findAdvisories(changes), advisoryError: null }
  } catch (error) {
    return { advisories: null, advisoryError: error.message }
  }
}

async function buildReport(lockfiles, commit) {
  const unreadable = lockfiles.filter(({ head }) => head && !isCheckableLockfile(head)).map(({ file }) => file)
  if (unreadable.length > 0) return { text: renderUnreadableReport({ commit, files: unreadable }), failed: true }

  const changes = lockfiles.flatMap(({ base, head }) => changedPackages(base, head))
  if (changes.length === 0) return null

  const { advisories, advisoryError } = await lookUpAdvisories(changes)
  const failed = Boolean(advisoryError) || hasAdvisories(advisories)
  return { text: renderReport({ commit, changes, advisories, advisoryError }), failed }
}

const { values: options } = parseArgs({
  options: { base: { type: 'string' }, head: { type: 'string' }, out: { type: 'string' } },
})
const headCommit = git('rev-parse', '--verify', `${options.head}^{commit}`).trim()
const report = await buildReport(changedLockfiles(options.base, options.head), headCommit)
if (report) {
  if (options.out) writeFileSync(options.out, report.text)
  console.log(report.text)
} else {
  console.log('No lockfile entry changed.')
}
process.exitCode = report?.failed ? 1 : 0
