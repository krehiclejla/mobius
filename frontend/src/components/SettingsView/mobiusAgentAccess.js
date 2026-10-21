/* Requests behind the Möbius provider page: model access, trial activation and
   picker visibility. The full account (profile, hosting, deployments) stays in
   the Möbius · You app; this page only reads the provider-facing slice. */
import { BASE, getAuthHeaders } from '../../api/client.js'

export class MobiusAccessError extends Error {
  constructor(message, status = 0, code = '') {
    super(message)
    this.name = 'MobiusAccessError'
    this.status = status
    this.code = code
  }
}

const ACCESS_STATES = ['signed_out', 'unavailable', 'available']

function isObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function boundedText(value, min, max) {
  return typeof value === 'string' && value.length >= min && value.length <= max
}

function parsePricing(pricing) {
  if (!isObject(pricing)) return null
  const kinds = ['input', 'cached_input', 'output']
  if (!kinds.every(kind => Number.isFinite(pricing[kind]) && pricing[kind] >= 0)) return null
  return { input: pricing.input, cached_input: pricing.cached_input, output: pricing.output }
}

/**
 * Read GET /api/identity/agent into exactly the fields this page renders.
 *
 * Unknown keys are ignored at every level so a field the identity service adds
 * later cannot blank the page. The one structural rule kept strictly is that
 * signed-out or unavailable access exposes no models, balance or trial.
 */
export function parseAgentAccess(value) {
  if (!isObject(value) || !ACCESS_STATES.includes(value.agent_access)) {
    throw new Error('Möbius returned invalid model access.')
  }
  const models = value.models ?? []
  const balance = value.balance ?? {}
  const trial = value.trial ?? {}
  const retention = value.retention ?? {}
  if (!Array.isArray(models) || ![balance, trial, retention].every(isObject)) {
    throw new Error('Möbius returned invalid model access.')
  }
  if (value.agent_access !== 'available') {
    if (models.length || 'available_units' in balance || 'available_usd' in balance || 'state' in trial) {
      throw new Error('Möbius exposed model data without account access.')
    }
    return { agent_access: value.agent_access, models: [], balance: {}, trial: {}, retention: {} }
  }

  if (models.length > 50) throw new Error('Möbius returned invalid model prices.')
  const seen = new Set()
  const parsedModels = models.map(model => {
    const pricing = isObject(model) ? parsePricing(model.pricing) : null
    if (!pricing || !boundedText(model.id, 1, 80) || !boundedText(model.name, 1, 80)) {
      throw new Error('Möbius returned invalid model prices.')
    }
    if (seen.has(model.id)) throw new Error('Möbius returned duplicate model aliases.')
    seen.add(model.id)
    return { id: model.id, name: model.name, pricing }
  })
  if (!boundedText(trial.state, 1, 40)) throw new Error('Möbius returned invalid trial state.')
  if (!Number.isSafeInteger(balance.available_units) || balance.available_units < 0) {
    throw new Error('Möbius returned an invalid model balance.')
  }
  if (!boundedText(retention.notice, 1, 500)) {
    throw new Error('Möbius returned invalid model retention state.')
  }
  return {
    agent_access: 'available',
    models: parsedModels,
    balance: { available_units: balance.available_units },
    trial: { state: trial.state },
    retention: { notice: retention.notice },
  }
}

/** One owner-authenticated request; agent-access responses are validated before use. */
export async function mobiusAccessRequest(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers: getAuthHeaders(options.headers),
    })
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new MobiusAccessError(
      'This Möbius could not reach its identity service. Check your connection and try again.',
    )
  }
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = body.detail
    throw new MobiusAccessError(
      typeof detail === 'string' ? detail : detail?.message || 'Möbius access is unavailable right now.',
      response.status,
      typeof detail?.code === 'string' ? detail.code : '',
    )
  }
  const pathname = path.split(/[?#]/, 1)[0]
  return pathname === '/api/identity/agent' || pathname === '/api/identity/agent/trial'
    ? parseAgentAccess(body)
    : body
}
