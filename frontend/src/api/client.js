// Thin client for the SpiderMonkey API (contract: backend/API.md). No retries, no fabricated data.

export const UPLOAD_TIMEOUT_MS = 180000
export const CHAT_TIMEOUT_MS = 120000
export const PROPOSE_TIMEOUT_MS = 60000

export class ApiError extends Error {
  constructor(status, detail) {
    const message = typeof detail === 'string' ? detail : detail?.message || `Request failed (HTTP ${status})`
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail // string, or an object such as the 409 {message, current_question, counts}
  }
}

export class TimeoutError extends Error {
  constructor(ms) {
    super(`No response after ${Math.round(ms / 1000)} s`)
    this.name = 'TimeoutError'
  }
}

export class NetworkError extends Error {
  constructor(cause) {
    super('Cannot reach the SpiderMonkey server')
    this.name = 'NetworkError'
    this.cause = cause
  }
}

export const SESSION_HEADER = 'X-Session-Id'
export const SESSION_ID_RE = /^[a-f0-9]{12}$/

async function request(path, { method = 'GET', json, form, timeoutMs = 30000, sessionId } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  const headers = {}
  if (json !== undefined) headers['Content-Type'] = 'application/json'
  if (sessionId) headers[SESSION_HEADER] = sessionId
  let res
  try {
    res = await fetch(path, {
      method,
      signal: ctrl.signal,
      headers: Object.keys(headers).length ? headers : undefined,
      body: json !== undefined ? JSON.stringify(json) : form,
    })
  } catch (err) {
    if (err?.name === 'AbortError') throw new TimeoutError(timeoutMs)
    throw new NetworkError(err)
  } finally {
    clearTimeout(timer)
  }
  let body = null
  try {
    body = await res.json()
  } catch {
    body = null
  }
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? body ?? null)
  return body
}

// Endpoints that are not about one session's research.
export const api = {
  health: () => request('/api/health'),
  sessions: () => request('/api/sessions'),
  createSession: (title = null) => request('/api/sessions', { method: 'POST', json: { title } }),
  // title === null reverts to the automatic title (follows the confirmed question)
  renameSession: (id, title) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: 'PATCH', json: { title } }),
  // Permanent. The server requires the id again as an explicit confirmation.
  deleteSession: (id) => request(`/api/sessions/${encodeURIComponent(id)}?confirm=${encodeURIComponent(id)}`,
    { method: 'DELETE' }),
}

/**
 * Client bound to ONE research session. Every request carries that session's id (X-Session-Id), fixed when the
 * client is created — so a request started in session A always reads/writes session A, even if the user has
 * switched to session B before it finishes. There is no default session.
 */
export function sessionApi(sessionId) {
  if (!SESSION_ID_RE.test(sessionId ?? '')) throw new Error('A valid research session id is required')
  const r = (path, opts = {}) => request(path, { ...opts, sessionId })
  return {
    sessionId,
    health: () => r('/api/health'),
    workspace: () => r('/api/workspace'),
    // Only ever called after the user confirmed the question. Changing the question of a project with papers
    // needs an explicit choice: keepPapers (keep papers + text, clear analysis) or reset (delete everything);
    // without one the server answers 409 {code: 'question_conflict', current_question, counts} and changes nothing.
    setQuestion: (question, { reset = false, keepPapers = false } = {}) =>
      r('/api/workspace', { method: 'PUT', json: { question, reset, keep_papers: keepPapers } }),
    // Gemini SUGGESTS a question from a broad interest; the server saves nothing.
    proposeQuestion: (interest, previous = []) =>
      r('/api/question/propose', { method: 'POST', json: { interest, previous }, timeoutMs: PROPOSE_TIMEOUT_MS }),
    // User-started re-analysis of one stored paper under the current question.
    analyzePaper: (paperId) =>
      r(`/api/papers/${encodeURIComponent(paperId)}/analyze`, { method: 'POST', timeoutMs: UPLOAD_TIMEOUT_MS }),
    graph: () => r('/api/graph'),
    checks: () => r('/api/checks'),
    latestImpact: async () => {
      try {
        return await r('/api/impact/latest')
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null // nothing evaluated yet
        throw err
      }
    },
    uploadPaper: (file, mode, { title } = {}) => {
      const form = new FormData()
      form.append('file', file)
      form.append('mode', mode)
      if (title) form.append('title', title)
      return r('/api/papers', { method: 'POST', form, timeoutMs: UPLOAD_TIMEOUT_MS })
    },
    // Is THIS exact PDF (full SHA-256) stored in this session, and is its analysis complete? Used after a timeout.
    lookupPaper: (sha256) => r(`/api/papers/lookup?sha256=${encodeURIComponent(sha256)}`),
    discover: (body = {}) => r('/api/discover', { method: 'POST', json: body, timeoutMs: 120000 }),
    chatHistory: () => r('/api/chat/messages'),
    // Permanent and confirmed by the user first. Each applies to THIS session only; the question is kept.
    clearChat: () => r('/api/chat/messages?confirm=true', { method: 'DELETE' }),
    // Every paper, its analysis AND the conversation (its citations point at the deleted text).
    clearPapers: () => r('/api/papers?confirm=true', { method: 'DELETE' }),
    // One paper and its analysis. Chat turns that used its evidence are kept but flagged.
    removePaper: (paperId) => r(`/api/papers/${encodeURIComponent(paperId)}?confirm=true`, { method: 'DELETE' }),
    chat: ({ message, context = null }) =>
      r('/api/chat', { method: 'POST', json: { message, context }, timeoutMs: CHAT_TIMEOUT_MS }),
  }
}

// Saved demo data in /public/fixtures. Fictional samples carry meta.source === "sample".
export async function loadFixture(name) {
  const res = await fetch(`/fixtures/${name}`)
  if (!res.ok) throw new Error(`Fixture ${name} not found`)
  return res.json()
}