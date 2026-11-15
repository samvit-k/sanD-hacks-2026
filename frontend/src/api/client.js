// Thin client for the ResearchLens API (contract: backend/API.md). No retries, no fabricated data.

export const UPLOAD_TIMEOUT_MS = 180000

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
    super('Cannot reach the ResearchLens server')
    this.name = 'NetworkError'
    this.cause = cause
  }
}

async function request(path, { method = 'GET', json, form, timeoutMs = 30000 } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  let res
  try {
    res = await fetch(path, {
      method,
      signal: ctrl.signal,
      headers: json !== undefined ? { 'Content-Type': 'application/json' } : undefined,
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

export const api = {
  health: () => request('/api/health'),
  workspace: () => request('/api/workspace'),
  setQuestion: (question, reset = false) => request('/api/workspace', { method: 'PUT', json: { question, reset } }),
  graph: () => request('/api/graph'),
  checks: () => request('/api/checks'),
  latestImpact: async () => {
    try {
      return await request('/api/impact/latest')
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
    return request('/api/papers', { method: 'POST', form, timeoutMs: UPLOAD_TIMEOUT_MS })
  },
  discover: (body = {}) => request('/api/discover', { method: 'POST', json: body, timeoutMs: 120000 }),
}

// Saved demo data in /public/fixtures. Fictional samples carry meta.source === "sample".
export async function loadFixture(name) {
  const res = await fetch(`/fixtures/${name}`)
  if (!res.ok) throw new Error(`Fixture ${name} not found`)
  return res.json()
}
