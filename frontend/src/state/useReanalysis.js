import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, TimeoutError } from '../api/client.js'

// User-started re-analysis of stored papers after a confirmed question change (keep_papers). Runs one
// paper at a time in the server's order (library first, evaluated papers last), refreshing the graph
// after each so it fills in progressively. Stops at the first failure; the failed paper and the rest stay
// "pending" on the server (their text is never deleted) and the user can continue later.
//
// Session safety: every request goes through `client`, which is bound to ONE research session, so no request can
// reach another project. When the workspace of that project unmounts (the user switched projects), the loop stops
// after the paper currently being analyzed: that request finishes on the server in its own project, and the
// remaining papers stay pending there until the user continues — nothing keeps running unseen.

export function useReanalysis({ client, refresh }) {
  const [state, setState] = useState({ running: false, done: 0, total: 0, current: null, error: null })
  const runningRef = useRef(false)
  const stopped = useRef(false)
  useEffect(() => {
    stopped.current = false
    return () => { stopped.current = true } // unmounted (project switched): start no further papers
  }, [])

  const start = useCallback(async (pending) => {
    const queue = (pending ?? []).filter(Boolean)
    if (runningRef.current || !queue.length) return false
    runningRef.current = true
    setState({ running: true, done: 0, total: queue.length, current: queue[0], error: null })
    let done = 0
    for (const paper of queue) {
      if (stopped.current) {
        runningRef.current = false
        return false
      }
      setState((s) => ({ ...s, current: paper }))
      try {
        await client.analyzePaper(paper.id)
      } catch (err) {
        const already = err instanceof ApiError && err.detail?.code === 'already_analyzed'
        if (!already) {
          const message = err instanceof TimeoutError
            ? `${err.message}. The server may still be working on it; wait a minute, then continue.`
            : err.message
          runningRef.current = false
          setState((s) => ({ ...s, running: false, current: null, error: { paper, message } }))
          await refresh({ fallback: false }).catch(() => {})
          return false
        }
      }
      done += 1
      setState((s) => ({ ...s, done }))
      await refresh({ fallback: false }).catch(() => {}) // a failed refresh never stops the analysis
    }
    runningRef.current = false
    setState((s) => ({ ...s, running: false, current: null }))
    return true
  }, [client, refresh])

  return { ...state, start }
}
