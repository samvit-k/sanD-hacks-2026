import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, NetworkError, TimeoutError, api } from './client.js'

const reply = (status, body) =>
  Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })

afterEach(() => vi.restoreAllMocks())

describe('api client', () => {
  it('returns JSON on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(200, { question: 'Q?' }))
    expect(await api.workspace()).toEqual({ question: 'Q?' })
  })

  it('parses string error details', async () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(422, { detail: 'No extractable text found in this PDF.' }))
    await expect(api.graph()).rejects.toMatchObject({ status: 422, message: 'No extractable text found in this PDF.' })
  })

  it('keeps the 409 question-conflict object', async () => {
    const detail = { message: 'Existing analysis…', current_question: 'Old?', counts: { papers: 3 } }
    vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(409, { detail }))
    const err = await api.setQuestion('New?').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.detail.current_question).toBe('Old?')
    expect(err.message).toBe('Existing analysis…')
  })

  it('sends the reset flag', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(200, {}))
    await api.setQuestion('Q?', true)
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({ question: 'Q?', reset: true })
  })

  it('treats a missing impact as null', async () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(404, { detail: 'none' }))
    expect(await api.latestImpact()).toBeNull()
  })

  it('distinguishes network errors from timeouts', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(api.health()).rejects.toBeInstanceOf(NetworkError)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    await expect(api.health()).rejects.toBeInstanceOf(TimeoutError)
  })

  it('uploads with the requested mode', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockReturnValue(reply(200, { mode: 'library' }))
    await api.uploadPaper(new Blob(['%PDF']), 'library', { title: 'T' })
    const form = spy.mock.calls[0][1].body
    expect(form.get('mode')).toBe('library')
    expect(form.get('title')).toBe('T')
  })
})
