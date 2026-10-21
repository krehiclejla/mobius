/* Möbius model access and picker visibility on the Möbius provider page. */
import { useCallback, useEffect, useState } from 'react'
import { mobiusAccessRequest } from './mobiusAgentAccess.js'
import './MobiusProviderAccess.css'

const dollars = units => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
}).format(units / 1_000_000)

const rate = value => new Intl.NumberFormat(undefined, {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4,
}).format(value)

function accessStatus(access, error) {
  if (!access) return error ? 'Unavailable' : 'Checking…'
  if (access.agent_access === 'signed_out') return 'Sign in needed'
  if (access.agent_access !== 'available') return 'Unavailable'
  return access.balance.available_units > 0 || access.trial.state === 'ready' ? 'Available' : 'No credit'
}

export default function MobiusProviderAccess({ active = true, onVisibilityChange, onOpenAccount }) {
  const [access, setAccess] = useState(null)
  const [accessError, setAccessError] = useState('')
  const [accessBusy, setAccessBusy] = useState(false)
  // Trial activation changes the Möbius account, so it confirms inline first,
  // like GitHub's Disconnect…. Leaving Settings drops the pending confirmation.
  const [confirmTrial, setConfirmTrial] = useState(false)
  const [visible, setVisible] = useState(null)
  const [visibilityError, setVisibilityError] = useState('')
  const [visibilityBusy, setVisibilityBusy] = useState(false)

  const loadAccess = useCallback(async () => {
    setAccessError('')
    try {
      setAccess(await mobiusAccessRequest('/api/identity/agent'))
    } catch (error) {
      setAccessError(error.message)
    }
  }, [])

  const loadVisibility = useCallback(async () => {
    setVisibilityError('')
    try {
      const result = await mobiusAccessRequest('/api/auth/providers/mobius/enabled')
      setVisible(result.enabled)
    } catch (error) {
      setVisibilityError(error.message)
    }
  }, [])

  useEffect(() => { void loadVisibility() }, [loadVisibility])
  // Signing in happens in Möbius · You, so re-read access whenever the owner
  // comes back to a visible Settings page.
  useEffect(() => {
    if (active) void loadAccess()
    else setConfirmTrial(false)
  }, [active, loadAccess])

  const setVisibility = async next => {
    if (visibilityBusy) return
    setVisibilityBusy(true)
    setVisibilityError('')
    try {
      const result = await mobiusAccessRequest('/api/auth/providers/mobius/enabled', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      setVisible(result.enabled)
      onVisibilityChange?.()
    } catch (error) {
      setVisibilityError(error.message)
    } finally {
      setVisibilityBusy(false)
    }
  }

  const activate = async () => {
    if (accessBusy) return
    setAccessBusy(true)
    setAccessError('')
    try {
      setAccess(await mobiusAccessRequest('/api/identity/agent/trial', { method: 'POST' }))
    } catch (error) {
      setAccessError(error.code === 'trial_fund_exhausted'
        ? 'Trial capacity is unavailable right now. Your account is still ready.'
        : error.message)
    } finally {
      setAccessBusy(false)
      setConfirmTrial(false)
    }
  }

  return <div className="settings-mobius-access">
    <label className="settings-mobius-access__visibility">
      <span>
        <strong>Show Möbius models in pickers</strong>
        <small>Hiding them also pauses existing Möbius chats until you turn them back on.</small>
      </span>
      <input type="checkbox" checked={visible === true} disabled={visible === null || visibilityBusy} onChange={event => void setVisibility(event.target.checked)} />
    </label>
    {visibilityError && <p className="settings-mobius-access__error" role="alert">{visibilityError} {visible === null && <button type="button" onClick={loadVisibility}>Retry</button>}</p>}

    <div className="settings-mobius-access__row">
      <span>Model access</span>
      <strong>{accessStatus(access, accessError)}</strong>
    </div>
    {access?.agent_access === 'signed_out' && <>
      <p className="settings-mobius-access__note">Sign in to your Möbius account in Möbius · You to use Möbius models here.</p>
      <button type="button" className="settings-mobius-access__action" onClick={onOpenAccount}>Sign in from Möbius · You</button>
    </>}
    {access?.agent_access === 'available' && <>
      {access.trial.state === 'ready' && !confirmTrial && <button type="button" className="settings-mobius-access__action" onClick={() => setConfirmTrial(true)}>Activate trial credit…</button>}
      {access.trial.state === 'ready' && confirmTrial && <div className="settings-mobius-access__confirm">
        <p className="settings-mobius-access__note">Activate your Möbius trial credit now? The trial starts when you activate it.</p>
        <div className="settings-mobius-access__actions">
          <button type="button" className="settings-mobius-access__action" disabled={accessBusy} onClick={() => setConfirmTrial(false)}>Cancel</button>
          <button type="button" className="settings-mobius-access__action" disabled={accessBusy} onClick={activate}>{accessBusy ? 'Activating…' : 'Activate'}</button>
        </div>
      </div>}
      <details className="settings-mobius-access__prices">
        <summary>Access details and token rates</summary>
        <div>
          <div className="settings-mobius-access__row"><span>Available credit</span><strong>{dollars(access.balance.available_units)}</strong></div>
          {access.trial.state !== 'ready' && access.balance.available_units <= 0 && <p className="settings-mobius-access__note">Credit is empty. Ask your Möbius provider to add more.</p>}
          <p className="settings-mobius-access__note">{access.retention.notice}</p>
          {access.models.map(model => <div className="settings-mobius-access__price" key={model.id}>
            <strong>{model.name}</strong>
            <span>{rate(model.pricing.input)} input · {rate(model.pricing.cached_input)} cached · {rate(model.pricing.output)} output</span>
          </div>)}
          <small>Rates are USD per million tokens.</small>
        </div>
      </details>
    </>}
    {accessError && <p className="settings-mobius-access__error" role="alert">{accessError} <button type="button" onClick={loadAccess}>Retry</button></p>}
  </div>
}
