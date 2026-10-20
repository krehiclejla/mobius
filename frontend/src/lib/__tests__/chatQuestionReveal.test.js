import assert from 'node:assert/strict'
import test from 'node:test'

import {
  chatQuestionRevealFor,
  questionRevealElement,
  consumeChatQuestionReveal,
  requestChatQuestionReveal,
  subscribeChatQuestionReveal,
} from '../chatQuestionReveal.js'

test('a notification reveal is local to its chat and is consumed exactly once', () => {
  let notices = 0
  const unsubscribe = subscribeChatQuestionReveal('chat-answer', () => { notices += 1 })
  const request = requestChatQuestionReveal('chat-answer')
  assert.ok(request?.id)
  assert.equal(chatQuestionRevealFor('chat-answer')?.id, request.id)
  assert.equal(chatQuestionRevealFor('other-chat'), null)
  assert.equal(consumeChatQuestionReveal('chat-answer', request.id), true)
  assert.equal(chatQuestionRevealFor('chat-answer'), null)
  assert.equal(consumeChatQuestionReveal('chat-answer', request.id), false)
  assert.equal(notices, 2)
  unsubscribe()
})

test('exact saved decision identity survives navigation and is consumed once', () => {
  const request = requestChatQuestionReveal('decision-source', 'already-answered-card')
  assert.equal(chatQuestionRevealFor('decision-source').questionId, 'already-answered-card')
  assert.equal(consumeChatQuestionReveal('decision-source', request.id), true)
})

test('exact-card focus never jumps to newer owner input if the target disappeared', () => {
  const settled = { dataset: { questionId: 'settled' } }
  const pending = { dataset: { questionId: 'pending' } }
  const root = { querySelectorAll: () => [settled, pending] }
  assert.equal(questionRevealElement(root, 'settled', pending), settled)
  assert.equal(questionRevealElement(root, 'missing', pending), null)
  assert.equal(questionRevealElement(root, null, pending), pending)
})


test('authoritative missing-anchor cleanup consumes only the matching navigation intent', async () => {
  const { requestChatSearchReveal, chatSearchRevealFor, retireMissingChatReveal, clearChatSearchReveal } =
    await import('../chatSearchReveal.js')
  const chatId = 'missing-decision'
  const search = requestChatSearchReveal(chatId, { anchorKey: 'older-search' })
  const decision = requestChatQuestionReveal(chatId, 'gone-card')
  assert.equal(retireMissingChatReveal(chatId, { decisionRevealId: decision.id, searchRevealId: search.id }), true)
  assert.equal(chatQuestionRevealFor(chatId), null)
  assert.equal(chatSearchRevealFor(chatId)?.id, search.id)
  const newer = requestChatQuestionReveal(chatId, 'another-card')
  assert.equal(retireMissingChatReveal(chatId, { decisionRevealId: decision.id, searchRevealId: search.id }), false)
  assert.equal(chatQuestionRevealFor(chatId)?.id, newer.id)
  assert.equal(chatSearchRevealFor(chatId)?.id, search.id)
  consumeChatQuestionReveal(chatId, newer.id)
  clearChatSearchReveal(chatId, search.id)
})
