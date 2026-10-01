/* First-run coach: teach and optionally set up Möbius while the shell stays usable. */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Download } from '@openai/apps-sdk-ui/components/Icon'
import { api } from '../../api/client.js'
import { ownerQueries } from '../../hooks/queries.js'
import { getInstallPromptSnapshot, requestInstall, subscribeInstallPrompt } from '../../lib/installPrompt.js'
import { prepareShellInstallPass } from '../../lib/shellInstallPass.js'
import { detectInstallPlatform, installCopyForPlatform } from '../../utils/installPlatform.js'
import { AgentSetup, HandleSetup } from './WalkthroughSetup.jsx'
import WalkthroughStore from './WalkthroughStore.jsx'
import './WalkthroughOverlay.css'

const SLIDES = ['welcome', 'connect', 'chat', 'apps', 'settings', 'identity']
const GUIDE_COUNT = SLIDES.length

export default function WalkthroughOverlay({ apps, storeActive = false, onOpenApp, onStoreSuspendedChange }) {
  const queryClient = useQueryClient()
  const closingRef = useRef(false)
  const titleRef = useRef(null)
  const cardRef = useRef(null)
  const pendingFocusRef = useRef(false)
  const wasSuspendedRef = useRef(false)
  const installAbortRef = useRef(null)
  const [stepIndex, setStepIndex] = useState(0)
  const [reviewingStore, setReviewingStore] = useState(false)
  const suspended = reviewingStore && storeActive
  const [platform] = useState(() => detectInstallPlatform())
  const [installCopy] = useState(() => installCopyForPlatform(platform))
  const [showInstallHelp, setShowInstallHelp] = useState(false)
  const [installBusy, setInstallBusy] = useState(false)
  const [installFeedback, setInstallFeedback] = useState('')
  const installState = useSyncExternalStore(subscribeInstallPrompt, getInstallPromptSnapshot, getInstallPromptSnapshot)
  const slide = SLIDES[stepIndex]

  // The shell's history restore must not queue chat-composer focus while this
  // guide is handing back from Store. Publish the lease at the same commit that
  // hides the guide, and release it on return or unmount.
  useLayoutEffect(() => {
    onStoreSuspendedChange?.(suspended)
    return () => { if (suspended) onStoreSuspendedChange?.(false) }
  }, [onStoreSuspendedChange, suspended])

  function finish() {
    if (closingRef.current) return
    closingRef.current = true
    queryClient.setQueryData(ownerQueries.walkthrough.key, previous => ({
      ...(previous || { completed_at: null }), completed: true,
    }))
    try { localStorage.setItem('mobius:walkthrough-completed', '1') } catch (_) {}
    api.owner.walkthrough.complete().catch(() => {})
  }

  function goTo(index) {
    pendingFocusRef.current = true
    setReviewingStore(false)
    setStepIndex(index)
    cardRef.current?.scrollTo({ top: 0 })
  }

  // Navigation and Store return announce the current step, but mounting a
  // modeless coach must not steal focus from the working shell.
  useEffect(() => {
    const active = document.activeElement
    const returnedFromStore = wasSuspendedRef.current && !suspended
    const focusWasReleased = active === document.body
      || active === document.documentElement
      || active?.id === 'main-content'
      || (active?.tagName === 'IFRAME'
        && active.closest?.('[data-app-frame-owner]')?.getAttribute('aria-hidden') === 'true')
    if (!suspended && (pendingFocusRef.current || (returnedFromStore && focusWasReleased))) {
      titleRef.current?.focus({ preventScroll: true })
      pendingFocusRef.current = false
    }
    wasSuspendedRef.current = suspended
  }, [stepIndex, suspended])

  useEffect(() => () => installAbortRef.current?.abort(), [])

  async function handleInstall() {
    setInstallFeedback('')
    if (platform.ios) {
      const controller = new AbortController()
      installAbortRef.current = controller
      setInstallBusy(true)
      await prepareShellInstallPass({ force: true, signal: controller.signal })
      if (controller.signal.aborted) return
      installAbortRef.current = null
      setInstallBusy(false)
    }
    if (installState !== 'ready') {
      setShowInstallHelp(value => !value)
      return
    }
    setInstallBusy(true)
    const result = await requestInstall()
    setInstallBusy(false)
    if (result.outcome === 'accepted') {
      setInstallFeedback('Installed on this device. Your guide is still here.')
      return
    }
    if (result.outcome === 'fallback-ready') {
      setInstallFeedback('Tap Install again to use your browser’s regular prompt.')
      return
    }
    setShowInstallHelp(true)
    setInstallFeedback(result.outcome === 'dismissed'
      ? 'Not installed. You can do this from your browser menu later.'
      : 'The browser prompt was unavailable. Use the steps below instead.')
  }

  const installLabel = installBusy ? 'Opening…' : installState === 'ready' ? 'Install' : showInstallHelp ? 'Hide' : installCopy.ctaLabel

  if (suspended) return null

  return <aside ref={cardRef} className="wt__card" role="region" aria-labelledby="wt-title">
      <div className="wt__topline">
        <div className="wt__brand"><span className="wt__mark" aria-hidden="true"><span /></span><span>Möbius / Getting started</span><span className="wt__count"><span aria-hidden="true">{String(stepIndex + 1).padStart(2, '0')} / {String(GUIDE_COUNT).padStart(2, '0')}</span><span className="sr-only">Step {stepIndex + 1} of {GUIDE_COUNT}</span></span></div>
        <button type="button" className="wt__close" onClick={finish} aria-label="Dismiss welcome" title="Dismiss guide">×</button>
      </div>
      <div className="wt__layout">
        <div className="wt__main">
          <div className="wt__slide" role="region" aria-labelledby="wt-title" tabIndex={0}>
        {slide === 'welcome' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Welcome to Möbius</h2>
          <p className="wt__lead">Möbius is a place to think out loud and make things happen.</p>
          <p className="wt__body">Bring a question, a rough idea, or a bigger ambition. Your agent can help you find a way forward, while apps give you tools to write, plan, build, and share. Möbius keeps your conversations and work together.</p>
          <p className="wt__body wt__body--second">This guide shows you around. Along the way, you can connect an agent and choose a public handle.</p>
          {installState !== 'installed' && <section className="wt__install" aria-labelledby="wt-install-title">
            <span className="wt__install-icon" aria-hidden="true"><Download width={19} height={19} /></span>
            <div><h3 id="wt-install-title">Keep Möbius close</h3><p>{installState === 'ready' ? 'Install it on this device for a full-screen, one-tap launch.' : installCopy.summary}</p></div>
            <button type="button" className="wt__install-btn" onClick={handleInstall} disabled={installBusy} aria-expanded={installState === 'ready' ? undefined : showInstallHelp} aria-controls={installState === 'ready' ? undefined : 'wt-install-help'}>{installLabel}</button>
            {showInstallHelp && <div className="wt__install-help" id="wt-install-help"><strong>{installCopy.title}</strong><span>{installCopy.body}</span></div>}
            {installFeedback && <p className="wt__install-feedback" role="status">{installFeedback}</p>}
          </section>}
          {installState === 'installed' && installFeedback && <p className="wt__install-feedback" role="status">{installFeedback}</p>}
        </>}

        {slide === 'connect' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Connect an agent</h2>
          <p className="wt__lead">Connect an agent to power Chat.</p>
          <p className="wt__body">Choose OpenAI Codex or Claude Code below, then follow the sign-in steps. Once connected, you can ask questions, plan work, and create things together. You can change your provider or model in Settings.</p>
          <AgentSetup />
        </>}

        {slide === 'chat' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Chat and Projects</h2>
          <p className="wt__lead">Start in Chat. Give bigger work a home in Projects.</p>
          <p className="wt__body">Ask a question or describe what you want to make in everyday words. When the work has several parts, create a Project to keep its chats, files, and finished results together. Your agent can use a Goal to show the plan, track progress, and pause when it needs a decision from you.</p>
          <div className="wt__feature-grid">
            <article><span>Ask and create</span><h3>Chat</h3><p>Ask a question, sketch an idea, or keep refining something in one conversation.</p></article>
            <article><span>Build over time</span><h3>Projects</h3><p>Start from scratch or a template, then keep every part of the work together.</p></article>
            <article><span>Track longer work</span><h3>Goals</h3><p>Follow multi-step work and see when your agent needs your input.</p></article>
          </div>
        </>}

        {slide === 'apps' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Explore apps</h2>
          <WalkthroughStore apps={apps} onReviewApp={(id, storeAppId) => {
            setReviewingStore(true)
            void onOpenApp(storeAppId, `app:${id}`)
          }} />
        </>}

        {slide === 'settings' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Your settings</h2>
          <p className="wt__lead">Set up Möbius the way you like it.</p>
          <p className="wt__body">In Settings, manage AI providers and models, choose models for background work, change the theme, check for updates, and sign out. Use the Integrations app to connect outside services.</p>
          <div className="wt__feature-grid wt__feature-grid--four">
            <article><span>Connect</span><h3>AI providers</h3><p>Connect or reconnect an agent and choose the model for Chat.</p></article>
            <article><span>Keep going</span><h3>Background agents</h3><p>Pick models for scheduled work from apps such as Memory and Reflection.</p></article>
            <article><span>Make it yours</span><h3>Appearance</h3><p>Choose light or dark mode.</p></article>
            <article><span>Stay current</span><h3>Möbius</h3><p>Check for platform updates and manage this installation.</p></article>
          </div>
          <p className="wt__footnote">Find Settings in the Möbius menu whenever you need it.</p>
        </>}

        {slide === 'identity' && <>
          <h2 id="wt-title" ref={titleRef} tabIndex={-1}>Choose a handle</h2>
          <p className="wt__lead">Give your Möbius profile a name people can recognize.</p>
          <p className="wt__body">Your @handle is the name others see when you share a page or write an app review. It helps them recognize you without showing your email address. Choose one below. You can change it later in Möbius · You.</p>
          <HandleSetup />
        </>}
          </div>

          <div className="wt__footer">
            {stepIndex > 0 && <button type="button" className="wt__back" onClick={() => goTo(stepIndex - 1)}>Back</button>}
            <button type="button" className="wt__next" onClick={() => stepIndex === SLIDES.length - 1 ? finish() : goTo(stepIndex + 1)}>
              {slide === 'identity' ? 'Finish guide' : 'Next'}
            </button>
          </div>
        </div>
      </div>
  </aside>
}
