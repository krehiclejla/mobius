/* Place a chat preview above its owning conversation, never above sibling panes. */
import { createPortal } from 'react-dom'

export default function ChatPanePortal({ anchorRef, children }) {
  const chat = anchorRef.current?.closest('.chat')
  return chat ? createPortal(children, chat) : null
}
