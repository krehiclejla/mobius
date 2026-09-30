const REQUEST_TYPES = new Set([
  'moebius:new-chat',
  'moebius:open-chat',
  'moebius:open-app',
  'moebius:open-settings',
  'moebius:projects',
  'moebius:chat-control',
])

/**
 * Whether the owner has just interacted with this document or a frame inside
 * it. A click in a (cross-origin) app frame also activates its ancestors, so
 * the shell can see that the owner acted without trusting the frame's word.
 * Browsers without the User Activation API report false (fail closed).
 */
export function hasTransientUserActivation(nav = globalThis.navigator) {
  return nav?.userActivation?.isActive === true
}

/**
 * Narrow the frame's navigation request wire format before it leaves the
 * exact-window-attributed AppCanvas boundary. Hosts receive one small, stable
 * contract rather than the frame's arbitrary postMessage object.
 *
 * `autoSend` submits the draft as the owner's own first message in a new
 * owner chat, which runs with the owner's full authority (including connected
 * services an app's own chats never receive). An app is untrusted code, so the
 * host honors it only when the caller vouches that the owner just acted in the
 * visible app (`mayAutoSend`); otherwise the text arrives as an editable draft.
 */
export function appHostRequest(message, { mayAutoSend = false } = {}) {
  if (!message || !REQUEST_TYPES.has(message.type)) return null
  if (message.type === 'moebius:new-chat') {
    return {
      type: message.type,
      draft: typeof message.draft === 'string' ? message.draft : '',
      autoSend: message.autoSend === true && mayAutoSend === true,
    }
  }
  if (message.type === 'moebius:open-chat') {
    if (typeof message.chatId !== 'string' || !message.chatId) return null
    return {
      type: message.type,
      chatId: message.chatId,
      ...(message.view === 'changes' ? { view: 'changes' } : {}),
      draft: typeof message.draft === 'string' ? message.draft : '',
    }
  }
  if (message.type === 'moebius:open-app') {
    if (!['string', 'number'].includes(typeof message.appId)) return null
    return {
      type: message.type,
      appId: message.appId,
      intent: typeof message.intent === 'string' ? message.intent : '',
    }
  }
  if (message.type === 'moebius:projects') {
    const actions = new Set(['templates', 'list', 'create', 'open', 'browse', 'import-sources', 'import-source'])
    if (
      typeof message.requestId !== 'string'
      || !/^projects:[a-z0-9]+:[a-z0-9]+$/i.test(message.requestId)
      || !actions.has(message.action)
    ) return null
    return {
      type: message.type,
      requestId: message.requestId,
      action: message.action,
      ...(message.action === 'import-source' ? { sourceId: typeof message.sourceId === 'string' ? message.sourceId.slice(0, 128) : '' } : {}),
      projectId: typeof message.projectId === 'string' ? message.projectId.slice(0, 128) : '',
      templateId: typeof message.templateId === 'string' ? message.templateId.slice(0, 128) : '',
      name: typeof message.name === 'string' ? message.name.trim().slice(0, 256) : '',
    }
  }
  if (message.type === 'moebius:chat-control') {
    const actions = new Set(['status', 'stop'])
    if (
      typeof message.requestId !== 'string'
      || !/^chat-control:[a-z0-9]+:[a-z0-9]+$/i.test(message.requestId)
      || !actions.has(message.action)
      || typeof message.chatId !== 'string'
      || !message.chatId.trim()
    ) return null
    return {
      type: message.type,
      requestId: message.requestId,
      action: message.action,
      chatId: message.chatId.trim().slice(0, 128),
    }
  }
  return {
    type: message.type,
    section: typeof message.section === 'string' ? message.section : '',
  }
}
