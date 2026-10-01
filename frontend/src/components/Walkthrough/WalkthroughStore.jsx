/* Discovery stays in the guide; App Store owns access review and installation. */
import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../../api/client.js'
import AppIcon from '../AppIcon.jsx'
import { findAppStoreApp } from '../../lib/appRecovery.js'

const CORE_IDS = ['store', 'social', 'memory', 'reflection', 'skills', 'integrations', 'identity']
const PICK_IDS = ['notes', 'habits', 'kanban', 'pages', 'webstudio', 'connect']
const CATALOG_URL = 'https://raw.githubusercontent.com/mobius-os/app-store/main/catalog.json'

function catalogItems(body) {
  if (body?.schema !== 1 || !Array.isArray(body.apps)) throw new Error('App Store listings are unavailable.')
  const items = new Map()
  for (const item of body.apps) {
    if (item && typeof item.id === 'string' && !items.has(item.id)) items.set(item.id, item)
  }
  return items
}

function storePicks(catalog) {
  return PICK_IDS.map(id => catalog?.get(id)).filter(item => item?.name && item?.description && item?.manifest_url && item?.raw_base)
}

function guideDescription(text) {
  return typeof text === 'string' ? text.replaceAll(';', ' —') : ''
}

function asDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

export default function WalkthroughStore({ apps, onReviewApp }) {
  const storeApp = findAppStoreApp(apps)
  const [catalog, setCatalog] = useState(null)
  const [catalogError, setCatalogError] = useState('')
  const [icons, setIcons] = useState({})
  const picks = useMemo(() => storePicks(catalog), [catalog])

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    async function load() {
      let hasLocal = false
      if (storeApp?.id) {
        try {
          const response = await apiFetch(`/apps/${storeApp.id}/source/file?path=catalog.json`, { signal: controller.signal, timeoutMs: 5000 })
          if (!response.ok) throw new Error('Local App Store catalog unavailable')
          const file = await response.json()
          const local = catalogItems(JSON.parse(file.content))
          if (active) { setCatalog(local); hasLocal = true }
        } catch (error) {
          if (error.name === 'AbortError') return
        }
      }
      try {
        const response = await apiFetch(`/proxy?url=${encodeURIComponent(CATALOG_URL)}`, { signal: controller.signal, timeoutMs: 8000 })
        if (!response.ok) throw new Error('Published App Store catalog unavailable')
        const remote = catalogItems(await response.json())
        if (active) { setCatalog(remote); setCatalogError('') }
      } catch (error) {
        if (active && !hasLocal) setCatalogError('App Store picks are not loading right now. You can explore the full collection in App Store later.')
      }
    }
    void load()
    return () => { active = false; controller.abort() }
  }, [storeApp?.id])

  useEffect(() => {
    if (!picks.length) return undefined
    let active = true
    const controller = new AbortController()
    for (const item of picks) {
      void (async () => {
        try {
          const response = await apiFetch(`/proxy?url=${encodeURIComponent(item.manifest_url)}`, { signal: controller.signal, timeoutMs: 8000 })
          if (!response.ok) return
          const manifest = await response.json()
          if (manifest.id !== item.id || typeof manifest.icon !== 'string') return
          const icon = new URL(manifest.icon, item.raw_base)
          if (icon.origin !== new URL(item.raw_base).origin || !icon.pathname.startsWith(new URL(item.raw_base).pathname)) return
          const iconResponse = await apiFetch(`/proxy?url=${encodeURIComponent(icon.href)}`, { signal: controller.signal, timeoutMs: 10_000 })
          if (!iconResponse.ok) return
          const image = await iconResponse.blob()
          if (!image.type.startsWith('image/')) return
          const iconData = await asDataUrl(image)
          if (active) setIcons(current => ({ ...current, [item.id]: iconData }))
        } catch (_) { /* Initials remain available when listing artwork is offline. */ }
      })()
    }
    return () => { active = false; controller.abort() }
  }, [picks])

  return <div className="wt__store">
    <p className="wt__lead">Find the tools you have and add the ones you want.</p>
    <p className="wt__body">Apps give you focused tools for writing, planning, building, and more. Open Apps to see everything installed, even if it is not pinned to your menu.</p>
    <h3 className="wt__section-heading">Included with Möbius</h3>
    <p className="wt__section-copy">Möbius includes these apps from the start.</p>
    <div className="wt__built-ins">
      {CORE_IDS.map(id => apps.find(app => app.slug === id)).filter(Boolean).map(app => <article className="wt__built-in-card" key={app.slug}>
        <AppIcon className="wt__built-in-icon" item={app} label={app.name} />
        <div><h3>{app.name}</h3><p>{guideDescription(app.description)}</p></div>
      </article>)}
    </div>
    <p className="wt__footnote">Memory and Reflection use a connected agent for scheduled work. You can browse Social right away, then join when you want to post.</p>
    <h3 className="wt__section-heading">Discover in the App Store</h3>
    <p className="wt__section-copy">Here are a few apps worth exploring. Review an app’s requested access in App Store before choosing to install it. Use Back or switch away from App Store to return to this guide.</p>
    {!catalog && !catalogError && <p className="wt__notice wt__store-loading" role="status">Loading App Store picks…</p>}
    {catalogError && <p className="wt__notice wt__store-loading" role="status">{catalogError}</p>}
    {catalog && picks.length === 0 && <p className="wt__notice wt__store-loading" role="status">These picks are not in the current App Store catalog. You can explore the full collection in App Store later.</p>}
    {!storeApp && <p className="wt__notice" role="status">App Store is unavailable. You can continue the guide without installing anything.</p>}
    {catalog && <div className="wt__store-grid">
      {picks.map(item => {
        const installed = apps.some(app => app.slug === item.id || app.source_manifest?.id === item.id)
        return <article className={`wt__store-pick${installed ? ' is-installed' : ''}`} key={item.id}>
          <AppIcon className="wt__store-icon" item={{ slug: item.id, icon_url: icons[item.id] }} label={item.name} size={null} />
          <h3>{item.name}</h3>
          <p className="wt__store-description">{guideDescription(item.description)}</p>
          <button type="button" className={installed ? 'wt__installed' : 'wt__action'} aria-label={installed ? `Installed ${item.name}` : `Review ${item.name} in App Store`} disabled={installed || !storeApp} onClick={() => onReviewApp(item.id, storeApp.id)}>{installed ? 'Installed' : 'Review in App Store'}</button>
        </article>
      })}
    </div>}
    <p className="wt__footnote">You do not need to install anything now. Find more apps in App Store whenever you are ready.</p>
  </div>
}
