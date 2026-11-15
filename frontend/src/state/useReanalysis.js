import { useCallback, useRef, useState } from 'react'
import { api, ApiError, TimeoutError } from '../api/client.js'

// User-started re-analysis of stored papers after a confirmed question change (keep_papers). Runs one
// paper at a time in the server's order (library first, evaluated papers last), refreshing the graph
// after each so it fills in progressively. Stops at the first failure; the failed paper and the rest stay
// "pending" on the server (their text is never deleted) and the user can continue later.

export function useReanalysis({ refresh }) {
  const [state, setState] = useState({ running: false, done: 0, total: 0, current: null, error: null })
  const runningRef = useRef(false)

  const start = useCallback(async (pending) => {
    const queue = (pending ?? []).filter(Boolean)
    if (runningRef.current || !queue.length) return false
    runningRef.current = true
    setState({ running: true, done: 0, total: queue.length, current: queue[0], error: null })
    let done = 0
    for (const paper of queue) {
      setState((s) => ({ ...s, current: paper }))
      try {
        await api.analyzePaper(paper.id)
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
  }, [refresh])

  return { ...state, start }
}
