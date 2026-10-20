/* Manual compaction owns action latches, bounded status reads, chat-scoped
 * recovery, and which Stop the composer means while context is prepared. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, jsonOrThrow } from '../../../api/client.js'
import { getOnlineSnapshot } from '../../../lib/connectivityStore.js'
import { isOwnerWorkspace } from '../../../lib/workspaceStorage.js'
import { composerDraftRevision } from '../composerDraft.js'
import { sendFailureMessage } from '../sendFailure.js'
import { isProviderSwitchBlocking } from '../providerSwitch.js'

const COMPACTION_PROGRESS_TIMEOUT_MS = 15000

export default function useCompactCommands({
  chatId, provisionalNewChat, hidden, embedded = false, serverCompactingKind,
  activationSettledRef, inputValueRef, setComposerInput, setSendFailure, fetchMessages, stopTurn,
  onCompacted,
}) {
  // A "/compact" submission is a chat action, not a turn: it rewrites the live
  // context instead of asking the model anything. Compact and Pause belong to
  // their chat, so a pending action in one chat never blocks another chat's.
  // A ``kind:chatId`` key is the synchronous same-chat duplicate latch, added
  // and removed only by the call that owns it; the state snapshot lets the
  // visible chat render its own outstanding compact POST.
  const pendingActionsRef = useRef(new Set())
  const [pendingActions, setPendingActions] = useState(() => new Set())
  const [compactProgressRecord, setCompactProgressRecord] = useState(null)
  const compactProgressRequestRef = useRef(0)
  const compactProgressControllerRef = useRef(null)
  const compactProgressActiveRef = useRef(false)
  const compactActionLifecycleRef = useRef(null)
  const activeCompactChatRef = useRef(chatId)
  activeCompactChatRef.current = chatId

  useEffect(() => {
    const lifecycle = { active: true }
    compactActionLifecycleRef.current = lifecycle
    return () => { lifecycle.active = false }
  }, [chatId])

  // The "/compact" command the owner submitted and this pane may clear. It is
  // consumed once, by whichever comes first: the server starting to compact or
  // the finished compaction. Only the call that set it may consume it.
  const submittedCommandRef = useRef(null)

  const clearSubmittedCommand = useCallback(() => {
    const submitted = submittedCommandRef.current
    submittedCommandRef.current = null
    if (!submitted?.lifecycle?.active || activeCompactChatRef.current !== submitted.chatId
      || !isOwnerWorkspace()
      || composerDraftRevision(submitted.chatId) !== submitted.revision
      || inputValueRef.current !== submitted.text) return
    setComposerInput('')
  }, [inputValueRef, setComposerInput])

  useEffect(() => {
    if (serverCompactingKind === 'compact') clearSubmittedCommand()
  }, [serverCompactingKind, clearSubmittedCommand])

  function claimAction(key) {
    if (pendingActionsRef.current.has(key)) return false
    pendingActionsRef.current.add(key)
    setPendingActions(new Set(pendingActionsRef.current))
    return true
  }

  function releaseAction(key) {
    pendingActionsRef.current.delete(key)
    setPendingActions(new Set(pendingActionsRef.current))
  }

  const cancelCompactProgress = useCallback(() => {
    ++compactProgressRequestRef.current
    compactProgressControllerRef.current?.abort()
    compactProgressControllerRef.current = null
  }, [])

  const refreshCompactProgress = useCallback(async (targetChatId = chatId) => {
    if (!targetChatId || provisionalNewChat || hidden || !compactProgressActiveRef.current
      || activeCompactChatRef.current !== targetChatId) return
    cancelCompactProgress()
    const controller = new AbortController()
    compactProgressControllerRef.current = controller
    const request = ++compactProgressRequestRef.current
    // Own the read deadline here: shared-browser transport forwards a caller
    // signal, but does not implement apiFetch's optional timeoutMs.
    const deadline = setTimeout(() => {
      const error = new Error('Compaction progress timed out')
      error.name = 'TimeoutError'
      controller.abort(error)
    }, COMPACTION_PROGRESS_TIMEOUT_MS)
    controller.signal.addEventListener('abort', () => clearTimeout(deadline), { once: true })
    try {
      const result = await jsonOrThrow(await api.chats.compactProgress(targetChatId, {
        signal: controller.signal,
      }), 'Compaction progress failed')
      if (!controller.signal.aborted && request === compactProgressRequestRef.current
        && activeCompactChatRef.current === targetChatId) {
        setCompactProgressRecord({ chatId: targetChatId, progress: result.progress || null })
      }
    } catch {
      // A failed status read must not erase the last known recovery handle.
    } finally {
      clearTimeout(deadline)
      if (compactProgressControllerRef.current === controller) {
        compactProgressControllerRef.current = null
      }
    }
  }, [chatId, provisionalNewChat, hidden, cancelCompactProgress])

  useEffect(() => {
    // Read on entry and on the existing compaction edge, including other tabs.
    // There is deliberately no polling and no automatic next POST. An embedded
    // chat has no manual compaction: its routes answer only the installation
    // owner, so an embedded read could only fail.
    compactProgressActiveRef.current = !embedded && !hidden && !provisionalNewChat
    void refreshCompactProgress(chatId)
    return () => {
      compactProgressActiveRef.current = false
      cancelCompactProgress()
    }
  }, [chatId, hidden, embedded, provisionalNewChat, serverCompactingKind, refreshCompactProgress, cancelCompactProgress])

  // "/compact" never becomes a message. It asks the backend to replace this
  // chat's live context with a fresh briefing and reset the provider session;
  // the visible transcript is untouched and the platform renders the stored
  // compaction as its own "Context compacted" card. A card action passes no
  // submitted input, so the owner's unrelated draft is left untouched.
  //
  // A submitted command stays in its chat's draft until the server reports it
  // has started compacting, and the progress card shows what is happening
  // from then on. A request the server refuses never starts compacting, so
  // rejection, transport failure, or leaving the chat never has to put the
  // command back: every pane of the chat already reads it. If that report
  // never reaches this pane, the finished compaction clears it instead.
  // Only the submitting pane, still showing its chat in the owner workspace
  // it submitted from, clears it, and only while the draft revision and text
  // are unchanged; any later edit (text, an emptied draft, or uploads) is
  // newer input. Otherwise the command deliberately stays as an editable
  // draft, saved and visible alike, and is never sent again on its own. Guest
  // drafts carry no revision, so an upload-only edit or a same-text cycle
  // would be invisible to the fence: they never auto-clear.
  async function runCompactCommand(instructions = '', submittedInput = '/compact', recoveryId = null) {
    if (!activationSettledRef.current) return
    if (!chatId || provisionalNewChat) {
      setSendFailure('There’s no chat context to compact yet.')
      return
    }
    if (serverCompactingKind === 'compact') return
    if (isProviderSwitchBlocking(chatId)) return
    const actionKey = `compact:${chatId}`
    if (!claimAction(actionKey)) return
    setSendFailure(null)
    const targetChatId = chatId
    const lifecycle = compactActionLifecycleRef.current
    const submitted = submittedInput !== null && isOwnerWorkspace()
      ? {
        chatId: targetChatId, lifecycle,
        text: inputValueRef.current, revision: composerDraftRevision(chatId),
      }
      : null
    if (submitted) submittedCommandRef.current = submitted
    const stillVisible = () => lifecycle?.active && activeCompactChatRef.current === targetChatId
    cancelCompactProgress()
    try {
      const result = await jsonOrThrow(await api.chats.compact(targetChatId, {
        ...(instructions ? { instructions } : {}),
        batch_id: crypto.randomUUID(),
        ...(recoveryId ? { recovery_id: recoveryId } : {}),
      }), 'Compaction failed')
      // The action response is authoritative even if a server edge started
      // an optional GET during its POST. That older read cannot replace it.
      if (stillVisible()) {
        cancelCompactProgress()
        setCompactProgressRecord({ chatId: targetChatId, progress: result.progress || null })
      }
      if (result.ok) {
        // The compaction rewrote what the context holds: a cached /context
        // breakdown must not survive it, whichever pane shows this chat.
        onCompacted?.(targetChatId)
        if (submitted && submittedCommandRef.current === submitted) clearSubmittedCommand()
        if (stillVisible()) await fetchMessages({ force: true })
      }
    } catch (err) {
      if (stillVisible()) setSendFailure(sendFailureMessage(err, { online: getOnlineSnapshot() }))
    } finally {
      if (submitted && submittedCommandRef.current === submitted) submittedCommandRef.current = null
      // Status readback is optional: it must never own the action latch.
      releaseAction(actionKey)
      if (lifecycle?.active) void refreshCompactProgress(targetChatId)
    }
  }

  async function stopCompactCommand() {
    const actionKey = `pause:${chatId}`
    if (!chatId || !claimAction(actionKey)) return
    const targetChatId = chatId
    const lifecycle = compactActionLifecycleRef.current
    cancelCompactProgress()
    try {
      await jsonOrThrow(await api.chats.compactStop(targetChatId), 'Pausing compaction failed')
    } catch (err) {
      if (lifecycle?.active && activeCompactChatRef.current === targetChatId) {
        setSendFailure(sendFailureMessage(err, { online: getOnlineSnapshot() }))
      }
    } finally {
      releaseAction(actionKey)
      if (lifecycle?.active) void refreshCompactProgress(targetChatId)
    }
  }

  // The server's compacting window spans the batch runner that makes a pane
  // read ``running``, so a reopened pane knows preparation is the running work.
  const preparing = pendingActions.has(`compact:${chatId}`)
    || serverCompactingKind === 'compact'
  const compactionProgress = (compactProgressRecord?.chatId === chatId && compactProgressRecord.progress)
    || (preparing ? { state: 'running' } : null)

  function continueCompaction() {
    const recoveryId = compactionProgress?.recovery_id
    if (recoveryId) void runCompactCommand('', null, recoveryId)
  }

  // Turn Stop clears queued input and waits on the transition lock the batch
  // holds, so during preparation the composer's Stop is Pause: queued input,
  // the draft and the previous session stay intact and no next batch starts.
  function stopComposerWork() {
    return preparing ? stopCompactCommand() : stopTurn()
  }

  return {
    preparing, compactionProgress,
    runCompactCommand, continueCompaction, stopCompactCommand, stopComposerWork,
  }
}
