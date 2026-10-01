/* Present hidden same-run interruptions as one reply without rewriting source rows. */
import { assistantAnchorKey, messageKey } from '../../lib/chatDetailCache.js'
import { assistantReplyRoot, isHiddenReplyCarrier, projectSteerContinuationMessage } from './steerContinuity.js'

/** Groups retain every source row/index, including the invisible delivery carriers.
 * Only a committed hidden steer can connect two explicit same-run identities. */
export function assistantReplyGroups(messages, { offset = 0, slots = new Map(), activeIndex = -1, activeKey, displayKeys = new Map() } = {}) {
  const groups = new Map()
  for (let start = 0; start < messages.length; start += 1) {
    const first = messages[start]
    if (first?.role !== 'assistant' || first.hidden) continue
    const rows = [{ message: first, index: start, notes: [] }]
    const root = assistantReplyRoot(first)
    let end = start
    while (root) {
      let next = end + 1
      let sawCarrier = false
      const notes = []
      while (next < messages.length) {
        const carrier = messages[next]
        if (!isHiddenReplyCarrier(carrier, root)) break
        sawCarrier = true
        notes.push(...(slots.get(next) || []))
        next += 1
      }
      const candidate = messages[next]
      if (!sawCarrier || assistantReplyRoot(candidate) !== root) break
      notes.push(...(slots.get(next) || []))
      rows.push({ message: candidate, index: next, notes })
      end = next
    }
    const lastVisibleIndex = rows.findLast(row => !row.message.hidden)?.index ?? -1
    const group = { start, end, lastVisibleIndex, rows: rows.map(row => ({
      ...row,
      key: row.index === activeIndex && activeKey ? activeKey
        : displayKeys.get(row.message.id) || messageKey(row.message, offset + row.index),
      anchorKey: assistantAnchorKey(offset + row.index),
    })) }
    for (let index = start; index <= end; index += 1) groups.set(index, group)
    start = end
  }
  return groups
}

/** The selected source already paints its question; only parallel saved rows dedup it. */
export function replyQuestionSuppression(questionKeys, activeRowIndex, rowIndex) {
  return rowIndex === activeRowIndex ? null : questionKeys
}

const sourceBlocks = message => Array.isArray(message.blocks) && message.blocks.length
  ? message.blocks : message.content ? [{ type: 'text', content: message.content }] : []

function hasTextPosition(notes, block, index) {
  return notes?.some(note => {
    const position = note.display_position
    return position?.text_offset > 0 || position?.block_index === (block.raw_index ?? index)
  })
}

/** Extend only an exact replay across an otherwise empty display seam. Real
 * thoughts/tools/timeline beats retain their position and existing safe cuts.
 * All rows keep their original keys and activity coordinates for restoration. */
export function presentAssistantReply(rows, { activeIndex = -1, positions = new Map() } = {}) {
  const presented = rows.map(row => ({ ...row, message: row.message }))
  let textOwner = null
  for (let index = 1; index < presented.length; index += 1) {
    const previous = rows[index - 1].message
    const current = rows[index].message
    const projected = projectSteerContinuationMessage(previous, current, { active: index === activeIndex })
    presented[index].message = projected
    const replay = projected?.steer_replay
    const before = sourceBlocks(previous)
    const after = sourceBlocks(current)
    const terminalIndex = before.length - 1
    const terminal = before[terminalIndex]
    const canJoin = replay && replay.text.startsWith(replay.prefix)
      && replay.textIndex === 0 && terminal?.type === 'text'
      && !previous.goal_summaries?.length && !previous.wait_summaries?.length
      && !current.continuation_reason && !current.wait_summaries?.length
      && !rows[index].notes.length
      && !hasTextPosition(positions.get(previous.id), terminal, terminalIndex)
      && !hasTextPosition(positions.get(current.id), after[0], 0)
    if (canJoin) {
      const owner = textOwner && terminalIndex === 0
        ? textOwner : { row: index - 1, block: terminalIndex }
      const ownerMessage = presented[owner.row].message
      const ownerBlocks = [...sourceBlocks(ownerMessage)]
      ownerBlocks[owner.block] = {
        ...ownerBlocks[owner.block], content: replay.text,
        reply_text_owner: true,
        reply_live_text: index === activeIndex && after.length === 1,
      }
      presented[owner.row].message = { ...ownerMessage, blocks: ownerBlocks }
      const nextBlocks = [...sourceBlocks(projected)]
      nextBlocks[0] = { ...nextBlocks[0], content: '', source_text_offset: replay.sourceOffset + replay.text.length }
      presented[index].message = {
        ...projected, blocks: nextBlocks, content: '',
        reply_text_owner_key: presented[owner.row].key,
      }
      textOwner = after.length === 1 ? owner : null
    } else {
      textOwner = null
    }
  }
  return presented
}
