// Own cancellable, live-required drawer reads for both chats and installed apps.

// The Shell's system stream reads both drawer lists live after every
// (re)connect, and discards any read begun before its subscription existed,
// because that read can miss the events that follow. A mount-time refetch of a
// persisted list always starts first, so it was always discarded — yet the
// service worker still finished that download, doubling a list that grows with
// every chat. Once the Shell mounts, its stream owns these refreshes. A list
// with no cached data still loads on mount. Defaults match by key prefix, and
// no other query key starts with these list keys.
export function letSystemStreamOwnListRefresh(queryClient, listQueries) {
  for (const queries of listQueries) {
    queryClient.setQueryDefaults(queries.keys.all, { refetchOnMount: false })
  }
}

export async function fetchFreshShellList(queryClient, queries, {
  signal,
  timeoutMs,
  reconcile = rows => rows,
} = {}) {
  signal?.throwIfAborted()
  // Do not join a pre-transition request and let its old snapshot overwrite a
  // committed question answer or run event. Cancellation is part of the read.
  await queryClient.cancelQueries({ queryKey: queries.keys.all })
  signal?.throwIfAborted()
  const data = await queryClient.fetchQuery({
    queryKey: queries.keys.all,
    queryFn: async ({ signal: querySignal }) => {
      const requestSignal = signal ? AbortSignal.any([signal, querySignal]) : querySignal
      const rows = await queries.list.fetch({
        timeoutMs, signal: requestSignal, cache: 'no-store',
      })
      // A replaced reconnect may finish decoding late. Its result must never
      // enter the shared query cache, even when the transport ignored abort.
      requestSignal.throwIfAborted()
      return reconcile(rows)
    },
    staleTime: 0,
    // The system connection owns recovery/backoff, not an overlapping query retry.
    retry: false,
  })
  return data || []
}
