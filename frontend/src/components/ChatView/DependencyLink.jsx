/* One quiet, visibly interactive route to the work or decision in another chat. */
import { goalDependencyHref } from './goalProgress.js'

export default function DependencyLink({ dependency, onInternalNav }) {
  const href = goalDependencyHref(dependency)
  const action = dependency.target_kind === 'owner_card' ? 'Open decision' : 'Open chat'
  if (dependency.source_deleted) return null
  if (!href) return <span className="chat__goal-task-meta">{dependency.source_chat_title || 'Another chat'}</span>
  return <a className="chat__dependency-link" href={href}
    aria-label={`${action}: ${dependency.condition} in ${dependency.source_chat_title}`}
    onClick={event => {
      if (!onInternalNav || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      onInternalNav(new URL(href, window.location.origin))
    }}>{action} · {dependency.source_chat_title}</a>
}
