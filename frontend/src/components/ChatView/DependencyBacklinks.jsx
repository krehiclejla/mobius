/* Quiet source context; following work never creates another owner request. */
import { ChevronRight } from '@openai/apps-sdk-ui/components/Icon'

export default function DependencyBacklinks({ chats = [], onInternalNav }) {
  if (!chats.length) return null
  const link = chat => {
    const href = `/shell/?${new URLSearchParams({ chat: chat.chat_id })}`
    return <a key={chat.chat_id} href={href} onClick={event => {
      if (!onInternalNav || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      onInternalNav(new URL(href, window.location.origin))
    }}>{chat.title}</a>
  }
  if (chats.length === 1) return <p className="chat__dependency-backlinks">Also needed by {link(chats[0])}</p>
  return <details className="chat__dependency-backlinks">
    <summary>Also needed by {chats.length} chats<ChevronRight className="chat__panel-chevron" width={14} height={14} aria-hidden="true" /></summary>
    <ul>{chats.map(chat => <li key={chat.chat_id}>{link(chat)}</li>)}</ul>
  </details>
}
