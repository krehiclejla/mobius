import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createServer } from 'vite'

// Render the whole Settings boundary with real React, not the transport hook shim.
const vite = await createServer({
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true, hmr: false, ws: false },
  ssr: { noExternal: ['@openai/apps-sdk-ui'] },
})
const { default: SettingsView } = await vite.ssrLoadModule('/src/components/SettingsView/SettingsView.jsx')
after(() => vite.close())

for (const failed of [false, true]) {
  test(`GitHub remains available while AI providers are ${failed ? 'unavailable' : 'loading'}`, () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false } } })
    try {
      if (failed) {
        for (const queryKey of [['settings'], ['auth', 'providers', 'status']]) {
          client.getQueryCache().build(client, { queryKey }).setState({
            status: 'error', error: new Error('AI provider service unavailable'), fetchStatus: 'idle',
          })
        }
      }
      const html = renderToStaticMarkup(createElement(QueryClientProvider, { client },
        createElement(SettingsView, { active: true })))
      assert.match(html, />GitHub</)
      assert.match(html, failed ? /Retry/ : /Loading providers/)
    } finally {
      client.clear()
    }
  })
}
