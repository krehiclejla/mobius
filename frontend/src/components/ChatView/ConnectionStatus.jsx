import { composerAdjacentActionProps } from './composerAdjacentAction.js'

/**
 * The chat's one connection notice: shown only once the stream has given up,
 * with the Retry the owner needs. Reattaching and retrying stay silent here;
 * the shell's connectivity badge owns "Reconnecting…", "Offline", and
 * "Restarting…".
 */
export default function ConnectionStatus({ error, onRetry }) {
  if (!error || error === 'retrying') return null
  // 'alert' (assertive) so a screen-reader user hears the lost connection
  // immediately and can find Retry.
  return (
    <div className="connection-status" role="alert" aria-live="assertive">
      <span className="connection-status__text">Connection lost</span>
      <button
        type="button"
        className="connection-status__retry"
        {...composerAdjacentActionProps(onRetry)}
      >
        Retry
      </button>
    </div>
  )
}
