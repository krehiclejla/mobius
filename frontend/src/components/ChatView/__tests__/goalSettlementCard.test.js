/* A Goal that stopped without arranging its next step reads as a calm handoff. */
import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createSsrRenderServer } from '../../../lib/__tests__/ssrRenderServer.mjs'

const vite = await createSsrRenderServer()
const { default: ErrorCard, errorCardViewModel } = await vite.ssrLoadModule('/src/components/ChatView/ErrorCard.jsx')
after(() => vite.close())

// The exact block the chat writer saves when bounded Goal settlement stops.
const settlementBlock = {
  type: 'error',
  code: 'goal_settlement_unfinished',
  resumable: true,
  message: 'The Goal is still unfinished: the agent ended without recording an '
    + 'outcome or saving its next handoff. Automatic settlement cannot safely '
    + 'continue. Resume to recover this execution; the Goal has not been declared impossible.',
}

test('goal settlement stop renders the calm Work interrupted card, not a red error', () => {
  const vm = errorCardViewModel(settlementBlock)
  assert.equal(vm.goalHandoff, true)
  assert.equal(vm.benign, true)
  assert.equal(vm.label, 'Work interrupted')
  assert.equal(vm.settlementRecovery, true)
  const html = renderToStaticMarkup(createElement(ErrorCard, { block: settlementBlock }))
  assert.match(html, /Your progress is saved/)
  assert.match(html, /This Goal is unfinished. Resume to continue/)
  assert.equal((html.match(/chat__recovery-title /g) || []).length, 1)
  assert.doesNotMatch(html, /role="alert"|settlement|execution/)
})

test('an unrelated error code keeps the ordinary error card', () => {
  const vm = errorCardViewModel({ ...settlementBlock, code: 'something_else' })
  assert.equal(vm.goalHandoff, false)
  assert.equal(vm.label, 'Error')
})
