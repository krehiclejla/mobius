// npm's bulk advisory endpoint, the one `npm audit` uses. It includes GitHub's
// malware advisories and returns only advisories that affect the exact
// versions asked about.

export const REGISTRY = 'https://registry.npmjs.org'

// <name>/-/<unscoped name>-<version>.tgz, allowing only characters a package
// name or version can hold, so no percent-encoding can point somewhere else.
const TARBALL_PATH = /^\/((?:@[\w.!~*'()-]+\/)?([\w.!~*'()-]+))\/-\/\2-([\w.+-]+)\.tgz$/

// The package and version a registry tarball URL downloads, or null for any
// other URL. It is parsed the way fetch parses it, so `..` segments resolve first.
export function registryTarball(resolved) {
  const url = URL.parse(resolved)
  const match = url?.origin === REGISTRY ? url.pathname.match(TARBALL_PATH) : null
  return match && { name: match[1], version: match[3] }
}

const isObject = value => typeof value === 'object' && value !== null && !Array.isArray(value)
const isAdvisoryList = list => Array.isArray(list) && list.every(isObject)

// A successful reply is a map from packages we asked about to advisory lists.
// Anything else means the scan didn't happen, so it must fail like an
// unreachable database rather than read as "no advisories".
function checkedAdvisories(reply, query) {
  const valid = isObject(reply) && Object.entries(reply).every(([name, list]) => Object.hasOwn(query, name) && isAdvisoryList(list))
  if (!valid) throw new Error('npm advisory lookup returned an unexpected response')
  return reply
}

export async function findAdvisories(changes) {
  const byName = Object.groupBy(changes, change => change.name)
  const query = Object.fromEntries(
    Object.entries(byName).map(([name, group]) => [name, group.map(change => change.version)]),
  )
  const response = await fetch(`${REGISTRY}/-/npm/v1/security/advisories/bulk`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(query),
    signal: AbortSignal.timeout(30_000),
  })
  if (!response.ok) throw new Error(`npm advisory lookup returned HTTP ${response.status}`)
  return checkedAdvisories(await response.json(), query)
}
