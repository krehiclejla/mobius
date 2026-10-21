import { test } from 'node:test'
import assert from 'node:assert/strict'
import { renderHook } from './react-hook-shim.mjs'
import { useGeneratedFiles } from '../useGeneratedFiles.js'

const picture = { name: 'generated-image-1.png', size: 9, mime_type: 'image/png',
  previewable: true, source_tool_use_id: 'ig_1', width: 1536, height: 1024 }
const step = { type: 'tool', tool: 'ImageGen', tool_use_id: 'ig_1', input: 'A red fox in snow' }
const message = (text, files = [picture]) => ({
  role: 'assistant',
  // Every block is rebuilt, as a streaming delta or a live-to-saved switch does.
  blocks: [
    { ...step },
    { type: 'generated_files', files: files.map(file => ({ ...file })) },
    { type: 'text', content: text },
  ],
})

test('an open picture keeps its list identity while the answer streams on', () => {
  const { result, rerender } = renderHook(useGeneratedFiles, message('Here is'))
  const open = result.current

  rerender(message('Here is your fox.'))
  assert.equal(result.current, open, 'an equal rebuild must not reset the open preview')
  assert.equal(result.current[0].alt, 'A red fox in snow')
  assert.equal(result.current[0].kind, 'generated')
})

test('a newly published file replaces the kept list', () => {
  const { result, rerender } = renderHook(useGeneratedFiles, message('One'))
  const first = result.current

  const second = { ...picture, name: 'generated-image-2.png', source_tool_use_id: 'ig_2' }
  rerender(message('Two', [picture, second]))
  assert.notEqual(result.current, first)
  assert.deepEqual(result.current.map(file => file.name), [picture.name, second.name])
  // No step named ig_2 is in the message, so the renderer's fallback applies.
  assert.equal(result.current[1].alt, undefined)
})

test('a message without files, or an owner message, has the one empty list', () => {
  const { result, rerender } = renderHook(useGeneratedFiles, { role: 'assistant', blocks: [] })
  const empty = result.current
  assert.deepEqual(empty, [])
  rerender({ role: 'user', blocks: message('x').blocks })
  assert.equal(result.current, empty)
})
