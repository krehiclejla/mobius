import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readMarkdownPreview } from '../markdownPreview.js'

test('Markdown preview bounds an ignored Range response', async () => {
  const response = new Response('a'.repeat(100), { status: 200 })
  assert.deepEqual(await readMarkdownPreview(response, 16), {
    text: 'a'.repeat(16), truncated: true,
  })
})

test('Markdown preview keeps short UTF-8 files complete', async () => {
  const response = new Response('# Résumé', { status: 206 })
  assert.deepEqual(await readMarkdownPreview(response, 100), {
    text: '# Résumé', truncated: false,
  })
})

test('Markdown preview rejects failed fetches', async () => {
  await assert.rejects(readMarkdownPreview(new Response('', { status: 404 }), 16), /HTTP 404/)
})
