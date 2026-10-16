/* A Goal that stopped without arranging its next step reads as a calm handoff. */
import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const vite = await createServer({
  appType: 'custom', logLevel: 'error',
  server: { middlewareMode: true, hmr: false, ws: false },
  ssr: { noExternal: ['@openai/apps-sdk-ui'] },
})
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

test('goal settlement stop renders the calm Goal paused card, not a red error', () => {
  const vm = errorCardViewModel(settlementBlock)
  assert.equal(vm.goalHandoff, true)
  assert.equal(vm.benign, true)
  assert.equal(vm.label, 'Goal paused')
  const html = renderToStaticMarkup(createElement(ErrorCard, { block: settlementBlock }))
  assert.match(html, /Your progress is saved/)
  assert.doesNotMatch(html, /role="alert"|settlement|execution/)
})

test('an unrelated error code keeps the ordinary error card', () => {
  const vm = errorCardViewModel({ ...settlementBlock, code: 'something_else' })
  assert.equal(vm.goalHandoff, false)
  assert.equal(vm.label, 'Error')
})
