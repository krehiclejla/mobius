/* Opening clears the new badge, not the unread dot or the list. */
import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import NotificationsView from '../NotificationsView.jsx'
import { notificationQueries } from '../../../hooks/queries.js'

test('one chronological list marks unread rows with a purple accent dot', () => {
  const queryClient = new QueryClient()
  queryClient.setQueryData(notificationQueries.list.key, {
    pages: [[
      { id: 'new', source_type: 'agent', title: 'Fresh item', sent_at: '2026-09-30T10:00:00Z', read_at: null },
      { id: 'old', source_type: 'agent', title: 'Older item', sent_at: '2026-09-29T12:00:00Z', read_at: '2026-09-29T13:00:00Z' },
    ]],
    pageParams: [null],
  })
  const html = renderToStaticMarkup(React.createElement(
    QueryClientProvider, { client: queryClient },
    React.createElement(NotificationsView, {
      active: true, unreadCount: 1, onMarkRead() {}, onMarkAllRead() {}, onDismiss() {},
    }),
  ))
  assert.match(html, /Mark all as read/)
  assert.match(html, /Fresh item[\s\S]*Older item/)
  assert.doesNotMatch(html, /notifications__group-label/)
  assert.match(html, /notifications__unread-dot/)
  assert.match(html, /notifications__row-item--unread/)
  assert.doesNotMatch(html, /Mark as read/)
})
