/* Real document thumbnails that unfold into a reader without leaving the chat. */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Download } from '@openai/apps-sdk-ui/components/Icon'
import { BASE, apiFetch } from '../../api/client.js'
import { mediaTokenParam } from '../../api/mediaToken.js'
import { StandardMarkdown } from './markdown/BlockRenderer.jsx'
import { MARKDOWN_EXCERPT_BYTES, MARKDOWN_READER_BYTES, readMarkdownPreview } from './markdownPreview.js'

const filePath = (chatId, name) =>
  `/chats/${encodeURIComponent(chatId)}/generated-files/${encodeURIComponent(name)}`

async function freshFileUrl(chatId, name, preview = false) {
  const token = await mediaTokenParam(chatId)
  if (!token) throw new Error('File access unavailable')
  return `${BASE}/api${filePath(chatId, name)}${token}${preview ? '&preview=true' : ''}`
}

function useCardVisibility(ref) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return undefined
    }
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: '180px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return visible
}

function stripPreviewMarkup(line) {
  return line
    .replace(/^\s{0,3}(?:#{1,6}\s*|>\s*|[-*+]\s*)/, '')
    .replace(/!?\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`~]/g, '')
    .trim()
}

export function markdownCardExcerpt(text) {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  const titleLine = lines.find(line => /^#{1,3}\s/.test(line)) || lines[0] || ''
  const bodyLine = lines.find(line => line !== titleLine && !/^#{1,6}\s/.test(line)
    && !/^```/.test(line) && !/^\|[-:\s|]+\|$/.test(line)) || ''
  return { title: stripPreviewMarkup(titleLine), body: stripPreviewMarkup(bodyLine) }
}

function PdfThumbnail({ chatId, file, visible }) {
  const canvasRef = useRef(null)
  const [status, setStatus] = useState('loading')

  useEffect(() => {
    if (!visible) return undefined
    let active = true
    let loadingTask = null
    let renderTask = null
    let page = null
    setStatus('loading')

    async function renderFirstPage() {
      const src = await freshFileUrl(chatId, file.name, true)
      if (!active) return
      const pdfjs = await import('pdfjs-dist')
      if (!active) return
      pdfjs.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/pdf.worker.mjs'
      // A collapsed card needs one page, not a copy of the whole PDF.
      loadingTask = pdfjs.getDocument({
        url: src,
        disableStream: true,
        disableAutoFetch: true,
      })
      const pdf = await loadingTask.promise
      if (!active) return
      page = await pdf.getPage(1)
      if (!active) return
      const canvas = canvasRef.current
      const context = canvas?.getContext('2d')
      if (!canvas || !context) throw new Error('Canvas unavailable')
      const natural = page.getViewport({ scale: 1 })
      const available = canvas.parentElement.clientWidth || 220
      const viewport = page.getViewport({ scale: available / natural.width })
      const outputScale = Math.min(2, window.devicePixelRatio || 1)
      canvas.width = Math.ceil(viewport.width * outputScale)
      canvas.height = Math.ceil(viewport.height * outputScale)
      canvas.style.width = `${viewport.width}px`
      canvas.style.height = `${viewport.height}px`
      renderTask = page.render({
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
      })
      await renderTask.promise
      if (active) setStatus('ready')
    }

    void renderFirstPage().catch(error => {
      if (active && error.name !== 'AbortError' && error.name !== 'RenderingCancelledException') {
        setStatus('error')
      }
    })
    return () => {
      active = false
      renderTask?.cancel?.()
      page?.cleanup?.()
      void loadingTask?.destroy?.()
    }
  }, [chatId, file.name, visible])

  return <div className="chat__document-card-pdf" aria-hidden="true">
    <canvas ref={canvasRef} className={status === 'ready' ? '' : 'is-loading'} />
    {status !== 'ready' && <span>{status === 'error' ? 'Preview unavailable' : 'Preparing first page…'}</span>}
  </div>
}

export default function DocumentAttachment({
  file, chatId, expanded, onToggle,
}) {
  const cardRef = useRef(null)
  const previewButtonRef = useRef(null)
  const collapseButtonRef = useRef(null)
  const focusAfterToggleRef = useRef(false)
  const visible = useCardVisibility(cardRef)
  const isMarkdown = file.mime_type === 'text/markdown'
  const [retry, setRetry] = useState(0)
  const [excerpt, setExcerpt] = useState({ status: 'loading', text: '' })
  const [report, setReport] = useState({ status: 'loading', text: '' })
  const [pdfPreview, setPdfPreview] = useState({ status: 'loading', src: '' })
  const [downloadError, setDownloadError] = useState(false)

  useEffect(() => {
    if (isMarkdown || !expanded) return undefined
    let active = true
    setPdfPreview({ status: 'loading', src: '' })
    freshFileUrl(chatId, file.name, true)
      .then(src => { if (active) setPdfPreview({ status: 'ready', src }) })
      .catch(() => { if (active) setPdfPreview({ status: 'error', src: '' }) })
    return () => { active = false }
  }, [chatId, expanded, file.name, isMarkdown, retry])

  async function download() {
    setDownloadError(false)
    try {
      const href = await freshFileUrl(chatId, file.name)
      const link = document.createElement('a')
      link.href = href
      link.download = file.name
      link.style.display = 'none'
      document.body.append(link)
      link.click()
      link.remove()
    } catch {
      setDownloadError(true)
    }
  }

  useEffect(() => {
    if (!isMarkdown || !visible) return undefined
    const controller = new AbortController()
    apiFetch(filePath(chatId, file.name), {
      signal: controller.signal,
      headers: { Range: `bytes=0-${MARKDOWN_EXCERPT_BYTES}` },
    })
      .then(response => readMarkdownPreview(response, MARKDOWN_EXCERPT_BYTES))
      .then(({ text }) => setExcerpt({ status: 'ready', text }))
      .catch(error => {
        if (error.name !== 'AbortError') setExcerpt({ status: 'error', text: '' })
      })
    return () => controller.abort()
  }, [chatId, file.name, isMarkdown, retry, visible])

  useEffect(() => {
    if (!isMarkdown || !expanded || report.status !== 'loading') return undefined
    const controller = new AbortController()
    apiFetch(filePath(chatId, file.name), {
      signal: controller.signal,
      headers: { Range: `bytes=0-${MARKDOWN_READER_BYTES}` },
    })
      .then(response => readMarkdownPreview(response, MARKDOWN_READER_BYTES))
      .then(({ text, truncated }) => setReport({ status: 'ready', text, truncated }))
      .catch(error => {
        if (error.name !== 'AbortError') setReport({ status: 'error', text: '' })
      })
    return () => controller.abort()
  }, [chatId, expanded, file.name, isMarkdown, report.status, retry])

  const cardExcerpt = isMarkdown && excerpt.status === 'ready'
    ? markdownCardExcerpt(excerpt.text)
    : null
  const kind = isMarkdown ? 'Markdown' : 'PDF'
  const size = `${Math.max(1, Math.round(file.size / 1024))} KB`

  const toggle = () => {
    focusAfterToggleRef.current = true
    onToggle()
  }

  // Keep focus on the control for this card when its own state changes.
  useLayoutEffect(() => {
    if (!focusAfterToggleRef.current) return
    focusAfterToggleRef.current = false
    const target = expanded ? collapseButtonRef.current : previewButtonRef.current
    target?.focus({ preventScroll: true })
  }, [expanded])

  return <article
    ref={cardRef}
    className={`chat__document-card${expanded ? ' chat__document-card--expanded' : ''}`}
  >
    <div className="chat__document-card-collapsed" hidden={expanded}>
      <button
        ref={previewButtonRef}
        type="button"
        className="chat__document-card-open"
        aria-label={`Expand ${file.name} preview`}
        aria-expanded={expanded}
        onClick={toggle}
      >
        <div className="chat__document-card-visual" aria-hidden="true">
          {isMarkdown ? <div className="chat__document-card-paper">
            {cardExcerpt ? <>
              <strong>{cardExcerpt.title || 'Markdown report'}</strong>
              <p>{cardExcerpt.body || 'Open to read this report.'}</p>
            </> : <span>{excerpt.status === 'error' ? 'Preview unavailable' : 'Preparing preview…'}</span>}
          </div> : <PdfThumbnail chatId={chatId} file={file} visible={visible} />}
        </div>
        <span className="chat__document-card-caption">
          <strong title={file.name}>{file.name}</strong>
          <small>{kind} · {size}</small>
        </span>
      </button>
      <button className="chat__document-card-download" type="button" onClick={download} aria-label={`Download ${file.name}`} title="Download">
        <Download width={17} height={17} aria-hidden="true" />
      </button>
    </div>
    {downloadError && <p className="chat__document-card-status" role="alert">Couldn’t download the file. Try again.</p>}
    {expanded && <>
      <header className="chat__document-card-toolbar">
        <div className="chat__document-card-title">
          <strong title={file.name}>{file.name}</strong>
          <span>{kind} · {size}</span>
        </div>
        <div className="chat__document-card-actions">
          <button type="button" onClick={download}>Download</button>
          <button ref={collapseButtonRef} type="button" onClick={toggle}>Collapse</button>
        </div>
      </header>
      {isMarkdown ? <div className="chat__document-card-reader" data-chat-scroll-region>
        {report.status === 'loading' && <p role="status">Loading Markdown preview…</p>}
        {report.status === 'error' && <div role="alert">
          <p>Couldn’t load the preview. You can try again or download the file.</p>
          <button type="button" onClick={() => {
            setReport({ status: 'loading', text: '' })
            setRetry(value => value + 1)
          }}>Try again</button>
        </div>}
        {report.status === 'ready' && <StandardMarkdown text={report.text} />}
        {report.status === 'ready' && report.truncated && (
          <p className="chat__document-card-status" role="status">
            Preview limited to 256 KB. Download the file to read the rest.
          </p>
        )}
      </div> : pdfPreview.status === 'ready'
        ? <iframe
            className="chat__document-card-iframe"
            title={`PDF preview: ${file.name}`}
            src={pdfPreview.src}
            referrerPolicy="no-referrer"
          />
        : <p className="chat__document-card-status" role="status">
          {pdfPreview.status === 'error' ? <>
            Couldn’t load the PDF preview. <button type="button" onClick={() => setRetry(value => value + 1)}>Try again</button>
          </> : 'Loading PDF preview…'}
        </p>}
    </>}
  </article>
}
