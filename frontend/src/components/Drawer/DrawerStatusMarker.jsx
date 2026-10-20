/* One marker slot preserves attention precedence and distinct lifecycle shapes. */
import { Pause, Stop } from '@openai/apps-sdk-ui/components/Icon'

export default function DrawerStatusMarker({ ownerRequired, streaming, waiting, onHold,
  holdReason, recovery, recoveryLabel, failed, building, attentionDot }) {
  return (ownerRequired ? (
    <span
      className="drawer__attention-diamond drawer__owner-input-dot"
      role="img"
      aria-label="Your input is needed"
      title="Your input is needed"
    />
  ) : streaming ? (
    <span
      className="drawer__streaming-dot"
      aria-label="Currently streaming"
      title="Currently streaming"
    />
  ) : waiting ? (
    <span
      className="drawer__waiting-icon"
      role="img"
      aria-label="Waiting to resume"
      title="Waiting to resume"
    >
      <Pause width={8} height={8} aria-hidden="true" />
    </span>
  ) : onHold ? (
    <span className="drawer__hold-icon" role="img" aria-label="On hold — continue when ready"
      title={holdReason || 'On hold — continue when ready'}>
      <Stop width={8} height={8} aria-hidden="true" />
    </span>
  ) : recovery ? (
    <span
      className="drawer__recovery-icon"
      role="img"
      aria-label={recoveryLabel}
      title={recoveryLabel}
    ><Pause width={8} height={8} aria-hidden="true" /></span>
  ) : failed ? (
    <span
      className="drawer__failure-dot"
      role="img"
      aria-label="Latest run failed"
      title="Latest run failed"
    />
  ) : building ? (
    <span
      className="drawer__streaming-dot"
      role="img"
      aria-label="Building"
      title="Building…"
    />
  ) : attentionDot ? (
    <span
      className="drawer__attention-dot"
      role="img"
      aria-label="New activity"
      title="New activity"
    />
  ) : null)
}
