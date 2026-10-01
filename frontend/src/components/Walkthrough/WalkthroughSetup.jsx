/* Inline first-run setup reuses the same provider flows and owner identity service as Settings and Möbius · You. */
import { useEffect, useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { authQueries } from '../../hooks/queries.js'
import { configuredProviderSet } from '../../lib/providerAvailability.js'
import { detailToMessage } from '../../lib/errorDetail.js'
import ProviderAuth from '../ProviderAuth/ProviderAuth.jsx'
import CodexAuth from '../ProviderAuth/CodexAuth.jsx'

export function AgentSetup() {
  const statusQuery = authQueries.provider.statuses.useQuery()
  const configured = configuredProviderSet(statusQuery.data)
  const [choice, setChoice] = useState('codex')

  return <div className="wt__setup">
    {statusQuery.isError ? <div className="wt__notice" role="alert">Connection status is unavailable right now. You can try again in Settings.</div>
      : statusQuery.isPending ? <div className="wt__notice" role="status">Checking your connections…</div>
        : configured.size > 0 ? <div className="wt__success" role="status">An agent is connected and ready for Chat.</div>
          : <>
            <div className="wt__choices" role="group" aria-label="Choose an AI provider">
              <button type="button" className={choice === 'codex' ? 'is-selected' : ''} aria-pressed={choice === 'codex'} onClick={() => setChoice('codex')}>OpenAI Codex</button>
              <button type="button" className={choice === 'claude' ? 'is-selected' : ''} aria-pressed={choice === 'claude'} onClick={() => setChoice('claude')}>Claude Code</button>
            </div>
            <div className="wt__setup-form">
              {choice === 'codex' ? <CodexAuth /> : <ProviderAuth authenticated={false} />}
            </div>
          </>}
    {statusQuery.data?.mobius?.available && !configured.has('mobius') && <p className="wt__footnote">Möbius · You also shows your Möbius agent access.</p>}
  </div>
}

export function HandleSetup() {
  const [identity, setIdentity] = useState(null)
  const [loading, setLoading] = useState(true)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    apiFetch('/identity', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Profile unavailable right now.')
      setIdentity(await response.json())
    }).catch(err => {
      if (err.name !== 'AbortError') setError(err.message || 'Profile unavailable right now.')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [])

  const canClaim = (identity?.account_mode === 'managed' || identity?.account_mode === 'linked')
    && !identity?.account_unavailable && Boolean(identity?.profile)
  const handle = identity?.profile?.handle
  const valid = /^[a-z0-9_]{3,30}$/.test(value)

  async function save(event) {
    event.preventDefault()
    if (!valid || saving) return
    setSaving(true)
    setError('')
    try {
      const response = await apiFetch('/identity/profile', {
        method: 'PATCH', body: JSON.stringify({ handle: value }), timeoutMs: 20_000,
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(detailToMessage(data.detail, 'Could not claim that handle.'))
      setIdentity(data)
    } catch (err) {
      setError(err.message || 'Could not claim that handle.')
    } finally {
      setSaving(false)
    }
  }

  return <div className="wt__setup">
    {loading ? <div className="wt__notice" role="status">Checking your profile…</div>
      : handle ? <div className="wt__success" role="status">You’re <strong>@{handle}</strong> on Möbius. Your email stays private.</div>
        : canClaim ? <form className="wt__handle-form" onSubmit={save}>
          <label htmlFor="wt-handle">Choose your handle</label>
          <div className="wt__handle-row"><span aria-hidden="true">@</span><input id="wt-handle" value={value} onChange={event => { setValue(event.target.value.toLowerCase()); setError('') }} maxLength={30} autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="yourname" aria-invalid={Boolean(error)} aria-describedby="wt-handle-hint" /></div>
          <p id="wt-handle-hint">Use 3 to 30 lowercase letters, numbers, or underscores. We’ll let you know if it’s already taken.</p>
          <button type="submit" className="wt__action" disabled={!valid || saving}>{saving ? 'Claiming…' : 'Claim handle'}</button>
        </form>
          : <div className="wt__notice">Link your Möbius account in Möbius · You to choose a handle.</div>}
    {error && <p className="wt__error" role="alert">{error}</p>}
  </div>
}
