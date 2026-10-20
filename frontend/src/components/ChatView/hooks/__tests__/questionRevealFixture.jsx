// Mounted browser harness for the production activation and reveal hooks.
import React, { useLayoutEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import '../../../../hooks/useNavigation.js'
import { useChatQuestionActivation, useChatQuestionReveal } from '../useChatQuestionReveal.js'
import { requestChatSearchReveal, chatSearchRevealFor, retireMissingChatReveal } from '../../../../lib/chatSearchReveal.js'
import { requestChatQuestionReveal, chatQuestionRevealFor } from '../../../../lib/chatQuestionReveal.js'

window.activations = []
window.reveals = []
window.requestDecision = id => requestChatQuestionReveal('source', id)
window.pendingDecision = () => chatQuestionRevealFor('source')
window.requestSearch = () => requestChatSearchReveal('source', { anchorKey: 'older-search-window' })
window.pendingSearch = () => chatSearchRevealFor('source')
window.reportMissingDecision = () => retireMissingChatReveal('source', {
  decisionRevealId: chatQuestionRevealFor('source')?.id,
  searchRevealId: chatSearchRevealFor('source')?.id,
})
function View({ id, hidden = false, ready = true, saved = 'saved:initial', search = null, card = 'choice' }) {
  const rootRef = useRef(null)
  const activation = useChatQuestionActivation({ chatId: 'source', hidden, searchRevealId: search?.id })
  const anchor = activation.reveal?.questionId ? `question:${activation.reveal.questionId}` : search?.anchor || saved
  useLayoutEffect(() => {
    if (!hidden) window.activations.push({ id, anchor })
  }, [id, hidden, anchor])
  useChatQuestionReveal({ activation, hidden, displayReady: ready, rootRef,
    pendingElement: null, contentVersion: card,
    revealElement: element => {
      window.reveals.push({ id, question: element.dataset.questionId })
      element.scrollIntoView()
    },
  })
  return <section ref={rootRef} id={id} hidden={hidden} data-anchor={anchor}>
    <div style={{ height: 1200 }} />
    {ready && <div tabIndex={-1} data-question-id={card}>Saved decision {card}</div>}
  </section>
}
let root = createRoot(document.getElementById('root'))
window.mountViews = views => root.render(<>{views.map(view => <View key={view.id} {...view} />)}</>)
window.remountViews = views => {
  root.unmount()
  root = createRoot(document.getElementById('root'))
  window.mountViews(views)
}
window.fixtureReady = true
