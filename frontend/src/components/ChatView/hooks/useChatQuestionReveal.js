// Exact-card intent belongs to one visible activation, never a saved reading position.
import { useCallback, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import {
  chatQuestionRevealFor, consumeChatQuestionReveal, questionRevealElement,
  subscribeChatQuestionReveal,
} from '../../../lib/chatQuestionReveal.js'

export function useChatQuestionActivation({ chatId, hidden, searchRevealId }) {
  const subscribe = useCallback(notify => subscribeChatQuestionReveal(chatId, notify), [chatId])
  const snapshot = useCallback(() => chatQuestionRevealFor(chatId), [chatId])
  const live = useSyncExternalStore(subscribe, snapshot, snapshot)
  const ref = useRef(null)
  if (!ref.current || ref.current.chatId !== chatId) {
    ref.current = { chatId, reveal: null, consumed: false, seenId: null, searchRevealId }
  }
  const current = ref.current
  const superseded = current.searchRevealId !== searchRevealId && searchRevealId != null
  current.searchRevealId = searchRevealId
  if (hidden) {
    current.reveal = null
    current.seenId = null
  } else if (superseded) {
    current.reveal = null
    current.seenId = live?.id ?? current.seenId
  } else if (live && live.id !== current.seenId) {
    current.reveal = live
    current.seenId = live.id
    current.consumed = false
  } else if (!live && !current.consumed) {
    // Another physical view may have received this intent first.
    current.reveal = null
  }
  return current
}

export function useChatQuestionReveal({ activation, hidden, displayReady, rootRef,
  pendingElement, revealElement, contentVersion }) {
  const { chatId, reveal, consumed } = activation
  useLayoutEffect(() => {
    // Retained hidden copies can have a complete DOM before the visible copy
    // has loaded. They must never focus or consume that copy's navigation.
    if (hidden || !displayReady || !reveal || consumed) return
    if (chatQuestionRevealFor(chatId)?.id !== reveal.id) return
    const element = questionRevealElement(rootRef.current, reveal.questionId, pendingElement)
    if (!element) return
    revealElement(element)
    if (reveal.questionId) element.focus({ preventScroll: true })
    // Retain the anchor through this activation's in-flight read; clearing it
    // here would immediately refetch the owner's saved reading position.
    activation.consumed = true
    consumeChatQuestionReveal(chatId, reveal.id)
  }, [activation, chatId, reveal, consumed, hidden, displayReady, rootRef,
    pendingElement, revealElement, contentVersion])
}
