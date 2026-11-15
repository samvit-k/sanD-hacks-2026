import { useCallback, useEffect, useMemo, useState } from 'react'
import { DEFAULT_SESSION } from './api/client.js'
import AssistantPanel from './components/assistant/AssistantPanel.jsx'
import GraphCanvas from './components/GraphCanvas.jsx'
import Legend from './components/Legend.jsx'
import TopBar from './components/TopBar.jsx'
import Inspector from './components/inspector/Inspector.jsx'
import WorkspacePanel from './components/workspace/WorkspacePanel.jsx'
import { DEFAULT_FILTERS, hiddenIds, matchingIds, nodeRefs } from './graph/toElements.js'
import { useChat } from './state/useChat.js'
import { useResearch } from './state/useResearch.js'
import { STATUS, useUploads } from './state/useUploads.js'

const NARROW_QUERY = '(max-width: 1100px)'

/** Human label for a graph item attached to a chat question. */
export function describeContext(context, graph, refs) {
  if (!context) return ''
  if (context.kind === 'question') return 'Central Question'
  if (context.kind === 'relationship') {
    const r = graph?.relationships?.[String(context.id)]
    return r ? `connection Node ${refs[`claim:${r.claim_a.id}`] ?? '?'} ↔ Node ${refs[`claim:${r.claim_b.id}`] ?? '?'}` : 'a connection'
  }
  if (context.kind === 'claim') return `Node ${refs[`claim:${context.id}`] ?? '?'} (claim)`
  if (context.kind === 'paper') return `Node ${refs[`paper:${context.id}`] ?? '?'} (paper)`
  return ''
}

function uploadNote(item) {
  const title = item.result?.paper?.title ?? item.name
  if (item.status === STATUS.failed) return { text: `Upload of ${item.name} did not complete: ${item.message}`, tone: 'warn' }
  if (item.mode === 'evaluate') {
    return { text: `Evaluated “${title}” against the library. Its impact on earlier findings is highlighted in the graph.`, tone: 'ok' }
  }
  const r = item.result ?? {}
  const detail = item.fromServer ? '' : ` (${r.claims_kept ?? 0} claims, ${r.relationships_created ?? 0} new relationships)`
  return { text: `Added “${title}” to the library${detail}.`, tone: 'ok' }
}

/** True while the viewport matches `query` (the sidebar collapses by default when narrow). */
function useNarrow(query) {
  const get = () => typeof window !== 'undefined' && !!window.matchMedia?.(query).matches
  const [narrow, setNarrow] = useState(get)
  useEffect(() => {
    const mq = window.matchMedia?.(query)
    if (!mq) return undefined
    const onChange = () => setNarrow(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return narrow
}

function DataBanner({ source, error, onRetry }) {
  if (source === 'sample') {
    return (
      <div className="banner banner-sample" role="status">
        <b>Sample data (fictional)</b> — the ResearchLens server is unreachable, so this view shows invented
        example papers and claims. Nothing here is real research evidence.
        <button type="button" className="link-btn" onClick={onRetry}>Retry connection</button>
      </div>
    )
  }
  if (source === 'snapshot') {
    return (
      <div className="banner" role="status">
        <b>Offline</b> — showing a saved snapshot of real pipeline output. Uploads are disabled.
        <button type="button" className="link-btn" onClick={onRetry}>Retry connection</button>
      </div>
    )
  }
  if (error) return <div className="banner banner-sample" role="alert">{error.message}</div>
  return null
}

export default function App() {
  const research = useResearch()
  const { graph, workspace, impact, checks, health, source, status, setImpact } = research
  const sessionId = DEFAULT_SESSION // Stage D: the selected research session
  const live = source === 'live'
  const chatEnabled = live && !!workspace?.question
  const chat = useChat(sessionId, { enabled: chatEnabled })
  const { addNote } = chat
  const onUploadSettled = useCallback((item) => {
    if (item.status === STATUS.done && item.mode === 'evaluate' && item.result?.impacts) setImpact(item.result)
    const note = uploadNote(item)
    addNote(note.text, { tone: note.tone, forSession: item.sessionId })
  }, [addNote, setImpact])
  const uploads = useUploads({ refresh: research.refresh, sessionId, onSettled: onUploadSettled })
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [chatContext, setChatContext] = useState(null)
  const [focusTick, setFocusTick] = useState(0)
  const [selection, setSelection] = useState(null)
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const narrow = useNarrow(NARROW_QUERY)
  const [manualCollapsed, setManualCollapsed] = useState(null) // null = follow the viewport
  const collapsed = manualCollapsed ?? narrow
  const toggleAssistant = () => {
    if (!assistantOpen && narrow) setManualCollapsed(true) // narrow screens: one side panel at a time
    setAssistantOpen(!assistantOpen)
  }
  const askAbout = (context) => {
    setChatContext(context)
    if (narrow) setManualCollapsed(true)
    setAssistantOpen(true)
    setFocusTick((t) => t + 1)
  }
  useEffect(() => setManualCollapsed(null), [narrow]) // crossing the breakpoint re-applies the default

  const refs = useMemo(() => nodeRefs(graph), [graph])
  const hidden = useMemo(() => hiddenIds(graph, filters), [graph, filters])
  const matches = useMemo(() => matchingIds(graph, query), [graph, query])
  const highlight = useMemo(() => {
    const out = {}
    for (const i of impact?.impacts ?? []) out[`claim:${i.prior_claim.id}`] = i.impact
    return out
  }, [impact])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') setSelection(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Select + centre an item. Also the hook future chat references ("Node 7", an edge) will call.
  const navigate = (sel) => setSelection(sel ? { ...sel, focus: true } : null)
  const focusNode = (nodeId) => {
    const node = graph?.nodes.find((n) => n.data.id === nodeId)?.data
    if (!node) return
    if (node.type === 'question') navigate({ kind: 'question', id: 'q' })
    else if (node.type === 'paper') navigate({ kind: 'paper', id: node.paper_id })
    else if (node.type === 'claim') navigate({ kind: 'claim', id: node.claim_id })
    else if (node.type === 'discovery') navigate({ kind: 'discovery', id: node.index })
  }
  const pickSearch = (hit) => focusNode(hit.id)
  // Chat reference -> graph element (Node N resolves through the stable refs carried by the server).
  const onReference = (ref) => {
    if (ref.kind === 'relationship') navigate({ kind: 'relationship', id: ref.id })
    else if (ref.kind === 'question') navigate({ kind: 'question', id: 'q' })
    else focusNode(ref.node_id)
  }
  const contextLabel = (ctx) => describeContext(ctx, graph, refs)
  const uploadBlockedReason = !live
    ? 'Uploads are disabled while showing offline or sample data. Retry the connection first.'
    : !workspace?.question ? 'Set a research question before uploading papers.' : ''
  const chatBlockedReason = !live
    ? 'The assistant needs the live server (offline or sample data is shown).'
    : 'Set a research question to start asking.'

  return (
    <div className={`app ${collapsed ? 'sb-collapsed' : ''} ${assistantOpen ? 'as-open' : ''}`}>
      <WorkspacePanel
        graph={graph}
        workspace={workspace}
        selection={selection}
        refs={refs}
        onSelectPaper={(id) => navigate({ kind: 'paper', id })}
        onSelectQuestion={() => navigate({ kind: 'question', id: 'q' })}
        collapsed={collapsed}
        onToggle={() => setManualCollapsed(!collapsed)}
      />
      <main className="main">
        <TopBar
          graph={graph} workspace={workspace} checks={checks} health={health} source={source}
          query={query} onQuery={setQuery} onPick={pickSearch} filters={filters} onFilters={setFilters}
          assistantOpen={assistantOpen} onToggleAssistant={toggleAssistant}
        />
        <DataBanner source={source} error={status === 'error' ? research.error : null} onRetry={research.refresh} />
        <section className="stage" aria-label="Evidence graph">
          {status === 'loading' && <div className="stage-note">Loading evidence graph…</div>}
          {graph && (
            <GraphCanvas
              graph={graph} hidden={hidden} matches={matches} selection={selection} highlight={highlight}
              onSelect={setSelection}
            />
          )}
          {graph && <Legend />}
          {graph && (
            <Inspector
              graph={graph} workspace={workspace} selection={selection} refs={refs}
              onNavigate={navigate} onClose={() => setSelection(null)} onAsk={chatEnabled ? askAbout : undefined}
            />
          )}
          {!assistantOpen && (
            <button type="button" className="as-tab" onClick={toggleAssistant} aria-controls="assistant-panel">
              Assistant
            </button>
          )}
        </section>
      </main>
      <AssistantPanel
        open={assistantOpen} onClose={() => setAssistantOpen(false)} chat={chat} uploads={uploads}
        canUpload={live && !!workspace?.question} uploadBlockedReason={uploadBlockedReason}
        hasLibrary={!!workspace?.has_library} chatEnabled={chatEnabled} chatBlockedReason={chatBlockedReason}
        context={chatContext} contextLabel={contextLabel} onClearContext={() => setChatContext(null)}
        onReference={onReference} focusTick={focusTick}
      />
    </div>
  )
}
