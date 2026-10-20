const TRUNCATE_AT = 80

// One-line preview for a queued message. A cut that would land between the
// two UTF-16 halves of an emoji backs off one unit, so the preview never ends
// in a lone surrogate (rendered as a replacement glyph).
export function queuedPreview(text) {
  const firstLine = text.split('\n')[0]
  const multiline = text.includes('\n')
  const needsTruncation = text.length > TRUNCATE_AT || multiline
  if (firstLine.length <= TRUNCATE_AT) {
    return { preview: firstLine + (multiline ? ' …' : ''), needsTruncation }
  }
  const splitsPair = /[\uD800-\uDBFF]/.test(firstLine[TRUNCATE_AT - 1])
  const end = splitsPair ? TRUNCATE_AT - 1 : TRUNCATE_AT
  return { preview: `${firstLine.slice(0, end)}…`, needsTruncation }
}

// What the tray shows for a message's attached files: the first name, plus how
// many more. Without it a message that is only a picture has no text at all
// and its row would be blank.
export function queuedFilesLabel(attachments) {
  const names = (attachments || []).map(file => file?.name).filter(Boolean)
  if (names.length === 0) return ''
  return names.length === 1 ? names[0] : `${names[0]} +${names.length - 1} more`
}
