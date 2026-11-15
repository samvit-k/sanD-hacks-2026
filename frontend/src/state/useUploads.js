import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, NetworkError, TimeoutError } from '../api/client.js'

// PDF upload queue on the existing ingestion endpoint (POST /api/papers).
//  - One upload at a time; honest states only (no invented percentages):
//      queued -> analyzing -> done | failed
//      analyzing --(browser timeout / lost connection)--> checking -> done | unknown
//  - Uncertain outcomes are reconciled against the server by the PDF's full SHA-256
//    (GET /api/papers/lookup): only THIS exact file counts — never "some new paper appeared".
//    A paper still being analyzed stays `unknown` with "Check again"; checks never overlap.
//  - Upload success is independent of the graph refresh that follows: if the refresh fails the
//    upload stays `done` and only the refresh can be retried (no re-upload, no re-analysis).
//  - Retrying an upload goes through the server's duplicate protection (HTTP 409), so it can never
//    overwrite an analysis that finished in the meantime.
//  - Each item remembers the session that started it (Stage D), for notices.

export const STATUS = {
  queued: 'queued', analyzing: 'analyzing', checking: 'checking', done: 'done', failed: 'failed', unknown: 'unknown',
}
const ACTIVE = new Set([STATUS.queued, STATUS.analyzing, STATUS.checking])
const BLOCKS_READD = new Set([...ACTIVE, STATUS.done, STATUS.unknown])

export const MESSAGES = {
  checking: 'Still processing or interrupted. Checking the server…',
  processing: 'The server is still analyzing this paper. Check again in a moment.',
  absent: 'This paper is not on the server: the upload may have failed or never arrived. You can retry it.',
  unreachable: 'Could not reach the server to confirm the result. Check again before retrying.',
  unidentified: 'Could not fingerprint this file to check it on the server. Check the paper list before retrying.',
  refreshFailed: 'Paper uploaded successfully, but the graph could not be refreshed.',
}

export const fileKey = (f) => `${f.name}:${f.size}:${f.lastModified ?? 0}`
const isPdf = (f) => f.type === 'application/pdf' || /\.pdf$/i.test(f.name ?? '')

/** Hex SHA-256 of a File (Web Crypto). Must match the server's hashlib.sha256 of the same bytes. */
function readBytes(file) {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer()
  return new Promise((resolve, reject) => { // older engines / jsdom
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(file)
  })
}

export async function sha256Hex(file) {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', await readBytes(file))
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

export function useUploads({ refresh, sessionId = 'default', onSettled, hashFile = sha256Hex } = {}) {
  const [items, setItems] = useState([])
  const running = useRef(false)
  const checking = useRef(new Set()) // keys with a reconciliation in flight
  const itemsRef = useRef(items)
  itemsRef.current = items
  const cb = useRef({ refresh, onSettled, hashFile })
  cb.current = { refresh, onSettled, hashFile }

  const patch = (key, changes) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...changes } : x)))
  const current = (key) => itemsRef.current.find((x) => x.key === key)

  /** Queue files. Returns { accepted: [names], rejected: [{name, reason}] }. */
  const add = useCallback((fileList, mode) => {
    const files = [...(fileList ?? [])]
    const accepted = []
    const rejected = []
    const existing = itemsRef.current
    let evalBusy = existing.some((x) => x.mode === 'evaluate' && ACTIVE.has(x.status))
    const next = []
    for (const f of files) {
      if (!isPdf(f)) {
        rejected.push({ name: f.name, reason: 'Only PDF files are accepted.' })
      } else if ([...existing, ...next].some((x) => fileKey(x.file) === fileKey(f) && BLOCKS_READD.has(x.status))) {
        rejected.push({ name: f.name, reason: 'This file was already added in this session.' })
      } else if (mode === 'evaluate' && evalBusy) {
        rejected.push({ name: f.name, reason: 'Evaluate one new paper at a time.' })
      } else {
        if (mode === 'evaluate') evalBusy = true
        next.push({ key: `${mode}|${fileKey(f)}`, file: f, name: f.name, size: f.size, mode, sessionId,
          status: STATUS.queued, startedAt: null, finishedAt: null, result: null, message: null,
          sha256: null, serverState: null, fromServer: false, refreshFailed: false, duplicate: false })
        accepted.push(f.name)
      }
    }
    // A failed earlier attempt of the same file+mode is replaced rather than listed twice.
    if (next.length) setItems((xs) => [...xs.filter((x) => !next.some((n) => n.key === x.key)), ...next])
    return { accepted, rejected }
  }, [sessionId])

  /** Refresh workspace + graph WITHOUT touching the upload's outcome. */
  const refreshAfter = useCallback(async (key) => {
    try {
      await cb.current.refresh?.({ fallback: false })
      patch(key, { refreshFailed: false })
      return true
    } catch {
      patch(key, { refreshFailed: true })
      return false
    }
  }, [])

  const finish = (key, changes) => {
    patch(key, { ...changes, finishedAt: Date.now() })
    const item = current(key)
    if (changes.status === STATUS.done || changes.status === STATUS.failed) cb.current.onSettled?.({ ...item, ...changes })
  }

  /** Ask the server whether THIS file (by SHA-256) was stored and finished. Never overlaps per item. */
  const reconcile = useCallback(async (key, sha256) => {
    if (checking.current.has(key)) return
    checking.current.add(key)
    patch(key, { status: STATUS.checking, message: MESSAGES.checking })
    try {
      if (!sha256) {
        finish(key, { status: STATUS.unknown, serverState: 'unidentified', message: MESSAGES.unidentified })
        return
      }
      let found
      try {
        found = await api.lookupPaper(sha256)
      } catch {
        finish(key, { status: STATUS.unknown, serverState: 'unreachable', message: MESSAGES.unreachable })
        return
      }
      if (found?.found && found.status === 'complete') {
        finish(key, { status: STATUS.done, fromServer: true, serverState: 'complete', result: { paper: found.paper },
          message: null })
        await refreshAfter(key)
      } else if (found?.found) {
        finish(key, { status: STATUS.unknown, serverState: 'processing', message: MESSAGES.processing })
      } else {
        finish(key, { status: STATUS.unknown, serverState: 'absent', message: MESSAGES.absent })
      }
    } finally {
      checking.current.delete(key)
    }
  }, [refreshAfter])

  const run = useCallback(async (item) => {
    running.current = true
    patch(item.key, { status: STATUS.analyzing, startedAt: Date.now(), message: null, serverState: null })
    let sha256 = null
    try {
      sha256 = await cb.current.hashFile(item.file)
    } catch {
      sha256 = null // reconciliation will say it cannot identify the file
    }
    patch(item.key, { sha256 })
    try {
      let result
      try {
        result = await api.uploadPaper(item.file, item.mode)
      } catch (err) {
        if (err instanceof TimeoutError || err instanceof NetworkError) {
          // Not a failure: the server may have finished or still be working. Check this exact file.
          await reconcile(item.key, sha256)
        } else if (err instanceof ApiError) {
          finish(item.key, { status: STATUS.failed, message: err.message, duplicate: err.detail?.code === 'duplicate' })
        } else {
          finish(item.key, { status: STATUS.failed, message: err?.message ?? 'Upload failed.' })
        }
        return
      }
      // The server confirmed the upload: it is done regardless of what the refresh does next.
      finish(item.key, { status: STATUS.done, result, message: result?.warning ?? null, serverState: 'complete' })
      await refreshAfter(item.key)
    } finally {
      running.current = false
      setItems((xs) => [...xs]) // let the queue effect start the next item
    }
  }, [reconcile, refreshAfter])

  useEffect(() => {
    if (running.current) return
    const next = items.find((x) => x.status === STATUS.queued)
    if (next) run(next)
  }, [items, run])

  const checkAgain = useCallback((key) => {
    const item = current(key)
    if (item && item.status === STATUS.unknown) return reconcile(key, item.sha256)
    return undefined
  }, [reconcile])

  const retryRefresh = useCallback((key) => {
    const item = current(key)
    if (item && item.status === STATUS.done) return refreshAfter(key)
    return undefined
  }, [refreshAfter])

  /** Re-upload: only after a failure or a completed server check that did not find the paper processing. */
  const canRetry = (item) => item.status === STATUS.failed ||
    (item.status === STATUS.unknown && item.serverState !== 'processing')
  const retry = useCallback((key) => {
    const item = current(key)
    if (item && canRetry(item) && !checking.current.has(key)) {
      patch(key, { status: STATUS.queued, message: null, finishedAt: null, serverState: null, duplicate: false })
    }
  }, [])

  const dismiss = useCallback((key) => {
    setItems((xs) => xs.filter((x) => x.key !== key || ACTIVE.has(x.status)))
  }, [])

  const busy = items.some((x) => ACTIVE.has(x.status))
  return { items, busy, add, retry, canRetry, checkAgain, retryRefresh, dismiss }
}
