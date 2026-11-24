import { useCallback } from 'react'

/**
 * Clearing a research project's papers (all, or one) after the user confirmed it in a dialog, and re-syncing the
 * view when the server reports that data was cleared elsewhere. Every request goes through `client`, which is bound
 * to THIS project; the server applies each clear to this project only.
 *
 * - `research`: useResearch() of this project (`refresh`).
 * - `chat`: useChat() of this project (`resetLocal`, `reload`, `addNote`).
 * - `setSelection` / `setChatContext`: state setters of the workspace; a removed paper may be selected or attached.
 *
 * `resync` is recreated on every render on purpose: callers store it in a ref, so it always uses the current hooks.
 */
export function useClearing({ client, research, chat, sessionId, setSelection, setChatContext }) {
  const { addNote } = chat

  const refreshAfterClear = useCallback(async (done) => {
    try {
      await research.refresh({ fallback: false })
      addNote(done, { tone: 'ok', forSession: sessionId })
    } catch {
      addNote(`${done} The view could not be refreshed; use Retry above the graph.`, { tone: 'warn', forSession: sessionId })
      research.refresh()
    }
  }, [research.refresh, addNote, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  const onClearPapers = useCallback(async () => {
    const res = await client.clearPapers()
    chat.resetLocal() // the server deleted the conversation with the papers
    setSelection(null)
    setChatContext(null)
    const n = res?.deleted?.papers ?? 0
    await refreshAfterClear(`All papers were cleared (${n} ${n === 1 ? 'paper' : 'papers'}) and the chat with them. The central question was kept.`)
  }, [client, chat.resetLocal, refreshAfterClear]) // eslint-disable-line react-hooks/exhaustive-deps

  const onRemovePaper = useCallback(async (paper) => {
    const res = await client.removePaper(paper.id)
    setSelection(null) // it may point at the paper, one of its claims or one of their relationships
    setChatContext(null)
    const flagged = res?.chat_messages_flagged ?? 0
    await refreshAfterClear(`Removed “${paper.title}”.${flagged ? ` ${flagged} chat ${flagged === 1 ? 'message is' : 'messages are'} marked “Evidence removed”.` : ''}`)
    chat.reload()
  }, [client, chat.reload, refreshAfterClear]) // eslint-disable-line react-hooks/exhaustive-deps

  // Another tab (or this one) cleared data while a request ran: the server stored nothing; show the current state.
  const resync = () => {
    chat.resetLocal()
    chat.reload()
    research.refresh()
  }

  return { onClearPapers, onRemovePaper, resync }
}