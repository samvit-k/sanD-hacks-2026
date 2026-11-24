import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useClearing } from './useClearing.js'

// The clearing handlers extracted from ResearchWorkspace: same calls, same order, same messages.
function setup({ refreshFails = false, clearPapers = { deleted: { papers: 3 } }, removePaper = { chat_messages_flagged: 2 } } = {}) {
  const calls = []
  const log = (name) => vi.fn((...args) => { calls.push([name, ...args]) })
  const client = {
    clearPapers: vi.fn(async () => { calls.push(['clearPapers']); return clearPapers }),
    removePaper: vi.fn(async (id) => { calls.push(['removePaper', id]); return removePaper }),
  }
  const research = {
    refresh: vi.fn(async (opts) => {
      calls.push(['refresh', opts])
      if (refreshFails && opts?.fallback === false) throw new Error('offline')
    }),
  }
  const chat = { addNote: log('addNote'), resetLocal: log('resetLocal'), reload: log('reload') }
  const setSelection = log('setSelection')
  const setChatContext = log('setChatContext')
  const { result } = renderHook(() => useClearing({ client, research, chat, sessionId: 'aaaaaaaaaaaa', setSelection, setChatContext }))
  return { result, calls, client }
}

describe('useClearing', () => {
  it('clear all papers: server first, then local chat reset, selection cleared, refresh, note', async () => {
    const { result, calls } = setup()
    await act(async () => { await result.current.onClearPapers() })
    expect(calls).toEqual([
      ['clearPapers'], ['resetLocal'], ['setSelection', null], ['setChatContext', null],
      ['refresh', { fallback: false }],
      ['addNote', 'All papers were cleared (3 papers) and the chat with them. The central question was kept.',
        { tone: 'ok', forSession: 'aaaaaaaaaaaa' }],
    ])
  })

  it('remove one paper: names the paper and the flagged messages, reloads the chat after the refresh', async () => {
    const { result, calls } = setup()
    await act(async () => { await result.current.onRemovePaper({ id: 'paper-a', title: 'AI tutor RCT' }) })
    expect(calls).toEqual([
      ['removePaper', 'paper-a'], ['setSelection', null], ['setChatContext', null], ['refresh', { fallback: false }],
      ['addNote', 'Removed “AI tutor RCT”. 2 chat messages are marked “Evidence removed”.',
        { tone: 'ok', forSession: 'aaaaaaaaaaaa' }],
      ['reload'],
    ])
  })

  it('singular wording, and no flag sentence when nothing was flagged', async () => {
    const one = setup({ clearPapers: { deleted: { papers: 1 } }, removePaper: { chat_messages_flagged: 1 } })
    await act(async () => { await one.result.current.onClearPapers() })
    await act(async () => { await one.result.current.onRemovePaper({ id: 'p', title: 'P' }) })
    const notes = one.calls.filter((c) => c[0] === 'addNote').map((c) => c[1])
    expect(notes).toEqual(['All papers were cleared (1 paper) and the chat with them. The central question was kept.',
      'Removed “P”. 1 chat message is marked “Evidence removed”.'])
    const none = setup({ removePaper: {} })
    await act(async () => { await none.result.current.onRemovePaper({ id: 'p', title: 'P' }) })
    expect(none.calls.find((c) => c[0] === 'addNote')[1]).toBe('Removed “P”.')
  })

  it('a failed refresh after a clear warns and falls back to a normal refresh', async () => {
    const { result, calls } = setup({ refreshFails: true })
    await act(async () => { await result.current.onClearPapers() })
    expect(calls.slice(-3)).toEqual([
      ['refresh', { fallback: false }],
      ['addNote', 'All papers were cleared (3 papers) and the chat with them. The central question was kept. '
        + 'The view could not be refreshed; use Retry above the graph.', { tone: 'warn', forSession: 'aaaaaaaaaaaa' }],
      ['refresh', undefined],
    ])
  })

  it('a server error propagates (the dialog shows it) and nothing local is cleared', async () => {
    const { result, calls, client } = setup()
    client.clearPapers.mockImplementationOnce(async () => { throw new Error('"X" is being analyzed right now.') })
    await expect(result.current.onClearPapers()).rejects.toThrow(/being analyzed/)
    expect(calls).toEqual([])
  })

  it('resync resets the local chat, reloads it and refreshes the project', () => {
    const { result, calls } = setup()
    act(() => { result.current.resync() })
    expect(calls).toEqual([['resetLocal'], ['reload'], ['refresh', undefined]])
  })
})