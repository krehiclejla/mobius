export const MARKDOWN_EXCERPT_BYTES = 8 * 1024
export const MARKDOWN_READER_BYTES = 256 * 1024

/** Bound both network reads and markdown rendering even if Range is ignored. */
export async function readMarkdownPreview(response, limit) {
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  if (!response.body) throw new Error('File preview unavailable')
  const reader = response.body.getReader()
  const bytes = new Uint8Array(limit)
  let length = 0
  let truncated = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      const remaining = limit - length
      const copied = Math.min(value.length, remaining)
      bytes.set(value.subarray(0, copied), length)
      length += copied
      if (value.length > remaining) {
        truncated = true
        break
      }
    }
  } finally {
    await reader.cancel()
  }
  return { text: new TextDecoder().decode(bytes.subarray(0, length)), truncated }
}
