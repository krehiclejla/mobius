/* An exact-edge owner control; never stops either chat's work or the Goal. */
import { useId, useState } from 'react'

export default function StopFollowingAction({ dependency, onStopFollowing }) {
  const [confirming, setConfirming] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const descriptionId = useId()
  const stop = async () => {
    if (pending) return
    setPending(true)
    setError('')
    try {
      await onStopFollowing(dependency.id)
      setConfirming(false)
    } catch (err) {
      setError(err?.message || 'Could not stop following. Try again.')
    } finally {
      setPending(false)
    }
  }
  return <span className="chat__dependency-stop">
    {confirming && <span id={descriptionId} className="chat__goal-task-meta">
      Stop following this result? Your Goal stays unfinished. This does not change the other chat or stop work already started.
    </span>}
    <span className="chat__dependency-actions">
      <button type="button" className="chat__dependency-link" disabled={pending}
        aria-label={`Stop following: ${dependency.condition}`}
        aria-describedby={confirming ? descriptionId : undefined}
        onClick={() => setConfirming(true)}>
        Stop following
      </button>
      {confirming && <button type="button" className="chat__dependency-link" disabled={pending}
        aria-label={`Confirm stop following: ${dependency.condition}`}
        aria-describedby={descriptionId}
        onClick={() => void stop()}>{pending ? 'Stopping…' : 'Confirm stop following'}</button>}
      {confirming && <button type="button" className="chat__dependency-link" disabled={pending}
        onClick={() => { setConfirming(false); setError('') }}>Keep following</button>}
    </span>
    {error && <span role="alert" className="chat__goal-task-meta">{error}</span>}
  </span>
}
