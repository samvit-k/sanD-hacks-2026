import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, NetworkError, TimeoutError } from '../api/client.js'

// Research sessions (projects): the list, the active one, and create / rename / delete.
// - The active session id is remembered per browser (localStorage, best effort) and always validated against
//   the server's list; an unknown or deleted id is never selected, stored, or used for requests.
// - `activeRef`, `state.activeId` and localStorage change together, and only for a valid id.
// - List loads are versioned: only the newest response is applied, so a slow, older list cannot overwrite a
//   newer one. A valid user selection, a creation and a deletion each bump the version, so a refresh that was
//   already in flight can never switch the user back to another project.
// - Deleting goes through the server, which requires the id again as confirmation; the UI adds its own explicit
//   confirmation step before calling `remove`. Once the server confirms, the deleted project is dropped from local
//   state immediately — even if the following list refresh fails, nothing keeps pointing at it.

export const STORAGE_KEY = 'spidermonkey.activeSession'
const LEGACY_STORAGE_KEY = 'researchlens.activeSession' // before the rename to SpiderMonkey

function readStored() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem(LEGACY_STORAGE_KEY)
  } catch {
    return null
  }
}

function store(id) {
  try {
    window.localStorage.removeItem(LEGACY_STORAGE_KEY)
    if (id) window.localStorage.setItem(STORAGE_KEY, id)
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* storage unavailable: the choice simply is not remembered */
  }
}

/** Keep `preferred` if it still exists, else the stored choice, else the most recently updated session. */
export function chooseActive(sessions, preferred) {
  const ids = new Set(sessions.map((s) => s.id))
  if (preferred && ids.has(preferred)) return preferred
  const stored = readStored()
  if (stored && ids.has(stored)) return stored
  return sessions[0]?.id ?? null
}

export function useSessions() {
  const [state, setState] = useState({ status: 'loading', sessions: [], activeId: null, error: null, offline: false, maintenance: false })
  const version = useRef(0)
  const activeRef = useRef(null)
  const sessionsRef = useRef([])

  /** The single place where the active session changes (always to a listed id, or null). */
  const apply = useCallback((sessions, activeId, extra = {}) => {
    sessionsRef.current = sessions
    activeRef.current = activeId
    store(activeId)
    setState((s) => ({ ...s, ...extra, sessions, activeId }))
  }, [])

  const load = useCallback(async (preferred) => {
    const v = ++version.current
    try {
      const res = await api.sessions()
      if (v !== version.current) return null // a newer load, selection, creation or deletion superseded this
      const sessions = res.sessions ?? []
      apply(sessions, chooseActive(sessions, preferred ?? activeRef.current),
        { status: 'ready', error: null, offline: false, maintenance: false })
      return sessions
    } catch (error) {
      if (v !== version.current) return null
      // maintenance: the server is up but its database still needs the one-time migration (HTTP 503 code).
      const maintenance = error instanceof ApiError && error.detail?.code === 'migration_required'
      const offline = !maintenance && (error instanceof NetworkError || error instanceof TimeoutError ||
        (error instanceof ApiError && error.status >= 500))
      setState((s) => ({ ...s, status: 'error', error, offline, maintenance }))
      return null
    }
  }, [apply])

  useEffect(() => {
    load()
  }, [load])

  /** Switch projects. Unknown ids are rejected without touching state, refs, storage or the load version. */
  const select = useCallback((id) => {
    if (!sessionsRef.current.some((s) => s.id === id)) return false
    version.current += 1 // a refresh already in flight must not override this choice
    apply(sessionsRef.current, id)
    return true
  }, [apply])

  const create = useCallback(async (title = null) => {
    const created = await api.createSession(title)
    // Known to exist now, even if the list refresh below fails.
    const sessions = [created, ...sessionsRef.current.filter((s) => s.id !== created.id)]
    version.current += 1 // any older in-flight list must not undo this
    apply(sessions, created.id, { status: 'ready', error: null })
    await load(created.id)
    return created
  }, [apply, load])

  const rename = useCallback(async (id, title) => {
    const updated = await api.renameSession(id, title)
    apply(sessionsRef.current.map((s) => (s.id === id ? { ...s, ...updated } : s)), activeRef.current)
    await load()
    return updated
  }, [apply, load])

  const remove = useCallback(async (id) => {
    const res = await api.deleteSession(id) // throws -> nothing changes locally
    const remaining = sessionsRef.current.filter((s) => s.id !== id)
    const nextActive = activeRef.current === id ? (remaining[0]?.id ?? null) : activeRef.current
    version.current += 1 // an older in-flight list still containing the deleted project must not return it
    apply(remaining, nextActive)
    await load(nextActive)
    return res
  }, [apply, load])

  const active = state.sessions.find((s) => s.id === state.activeId) ?? null
  return { ...state, active, reload: load, select, create, rename, remove }
}