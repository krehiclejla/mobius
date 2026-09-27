const APP_STORE_MANIFEST_PREFIX = 'https://raw.githubusercontent.com/mobius-os/app-store/'

export function appCrashReportDraft(name, error) {
  const detail = String(error ?? '').trim()
    || 'Unknown app error (no message reached the shell)'
  return `The app "${name}" crashed with this error:\n\`\`\`\n${detail}\n\`\`\`\nPlease investigate and fix.`
}

export function findAppStoreApp(apps) {
  let nameFallback = null
  for (const app of Array.isArray(apps) ? apps : []) {
    if (String(app?.manifest_url || '').startsWith(APP_STORE_MANIFEST_PREFIX)) {
      return app
    }
    if (nameFallback == null && app?.name === 'App Store') nameFallback = app
  }
  return nameFallback
}
