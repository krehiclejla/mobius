/* ArchivedChatNotice tells the owner an open chat is filed under Archived and
   offers Restore; sending a message restores it too, so the composer stays live. */

import { Archive } from '@openai/apps-sdk-ui/components/Icon'

export default function ArchivedChatNotice({ onRestore }) {
  return (
    <section className="chat__waits chat__archived" aria-label="Archived chat">
      <div className="chat__wait-card chat__archived-card">
        <p className="chat__archived-text">
          <span className="chat__progress-identity" aria-hidden="true">
            <Archive width={14} height={14} />
          </span>
          <span>Archived · Sending a message restores it</span>
        </p>
        {onRestore && (
          <button
            type="button"
            className="chat__archived-restore"
            onPointerDown={(event) => event.preventDefault()}
            onClick={onRestore}
          >
            Restore
          </button>
        )}
      </div>
    </section>
  )
}
