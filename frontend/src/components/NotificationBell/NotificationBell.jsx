import { Bell } from '@openai/apps-sdk-ui/components/Icon'
import './NotificationBell.css'

// The header bell — lives in the shell bar's right-side action slot
// (shell__bar-actions), so it renders identically on desktop and mobile by
// construction. Opening acknowledges arrivals, but does not mark rows read.
export default function NotificationBell({
  newCount = 0, active = false, buttonRef, onClick,
}) {
  const count = Number.isFinite(newCount) && newCount > 0 ? newCount : 0
  const label = active
    ? 'Close notifications'
    : (count > 0 ? `Notifications, ${count} new` : 'Notifications')
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`notification-bell${active ? ' notification-bell--active' : ''}`}
      aria-label={label}
      aria-expanded={active}
      aria-controls="notification-preview"
      title={label}
      onClick={onClick}
    >
      <Bell width={18} height={18} aria-hidden="true" />
      {count > 0 && (
        <span className="notification-bell__badge" aria-hidden="true">
          {count > 99 ? '99+' : count}
        </span>
      )}
    </button>
  )
}
