import { useRef } from 'react'

const NO_FILES = Object.freeze([])
const FILE_FIELDS = [
  'name', 'size', 'mime_type', 'previewable', 'sha256',
  'source_tool_use_id', 'width', 'height', 'alt',
]

/** Every file an assistant message has published, newest last. A native
 * picture borrows its alt text from the prompt of the ImageGen step that made
 * it; the renderer falls back to "Generated image". */
export function generatedFilesOf(msg) {
  const blocks = msg?.role === 'assistant' && Array.isArray(msg.blocks) ? msg.blocks : NO_FILES
  if (!blocks.some(block => block?.type === 'generated_files')) return NO_FILES
  const prompts = new Map(blocks.flatMap(block => (
    block?.type === 'tool' && block.tool === 'ImageGen'
      && typeof block.tool_use_id === 'string'
      && typeof block.input === 'string' && block.input.trim()
      ? [[block.tool_use_id, block.input.trim()]]
      : []
  )))
  return blocks.flatMap(block => (
    block?.type === 'generated_files' && Array.isArray(block.files)
      ? block.files.map(file => {
        const alt = prompts.get(file.source_tool_use_id)
        return { ...file, kind: 'generated', ...(alt ? { alt } : {}) }
      })
      : []
  ))
}

function sameFiles(a, b) {
  return a.length === b.length && a.every((file, index) => (
    FILE_FIELDS.every(field => file[field] === b[index][field])
  ))
}

/** One list identity per message for as long as its files are unchanged.
 * Open image previews and document cards below this list are keyed by it, and
 * a streaming answer (or a live-to-saved surface switch) rebuilds the message
 * blocks on every delta; an equal rebuild must not reset what the reader has
 * open, re-decode it, or move the scroll position. */
export function useGeneratedFiles(msg) {
  const next = generatedFilesOf(msg)
  const stable = useRef(next)
  if (!sameFiles(stable.current, next)) stable.current = next
  return stable.current
}
