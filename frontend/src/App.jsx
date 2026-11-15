import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { sessionApi } from './api/client.js'
import AssistantPanel from './components/assistant/AssistantPanel.jsx'
import GraphCanvas from './components/GraphCanvas.jsx'
import Legend from './components/Legend.jsx'
import TopBar from './components/TopBar.jsx'
import Inspector from './components/inspector/Inspector.jsx'
import QuestionDialog from './components/question/QuestionDialog.jsx'
import SessionSwitcher from './components/workspace/SessionSwitcher.jsx'
import WorkspacePanel from './components/workspace/WorkspacePanel.jsx'
import { DEFAULT_FILTERS, hiddenIds, matchingIds, nodeRefs } from './graph/toElements.js'
import { useChat } from './state/useChat.js'
import { useReanalysis } from './state/useReanalysis.js'
import { useResearch } from './state/useResearch.js'
import { useSessions } from './state/useSessions.js'
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

function DataBanner({ source, error, onRetry, onExitDemo }) {
  if (source === 'sample') {
    return (
      <div className="banner banner-sample" role="status">
        <b>Demo mode — sample data (fictional)</b> — you chose to explore invented example papers and claims.
        Nothing here is real research evidence, and none of your projects are shown.
        <button type="button" className="link-btn" onClick={onExitDemo}>Exit demo</button>
      </div>
    )
  }
  if (source === 'snapshot') {
    return (
      <div className="banner" role="status">
        <b>Demo mode</b> — showing a saved snapshot of earlier pipeline output, not your live projects. Uploads are disabled.
        <button type="button" className="link-btn" onClick={onExitDemo}>Exit demo</button>
      </div>
    )
  }
  if (error) {
    return (
      <div className="banner banner-sample" role="alert">
        <b>Unable to load this project from the server.</b> {error.message} Nothing was changed.
        <button type="button" className="link-btn" onClick={onRetry}>Retry</button>
      </div>
    )
  }
  return null
}

/** What keeps running if the user leaves this project now (shown before switching). */
export function describeBusy({ uploads, reanalysis, chat }) {
  if (reanalysis.running) {
    return { label: 'A re-analysis', detail: 'The paper being analyzed now finishes on the server in this project; the '
      + 'remaining papers stay pending here until you continue the re-analysis.' }
  }
  if (uploads.busy) {
    return { label: 'An upload', detail: 'The paper being uploaded finishes on the server and is added to this project; '
      + 'uploads still waiting in the queue are not started.' }
  }
  if (chat.pending) {
    return { label: 'An assistant answer', detail: 'The answer is still saved to this project\'s conversation.' }
  }
  return null
}

/**
 * One research project. Mounted with key = session id, so switching projects discards all of this state: a late
 * response from the previous project can never update the new one. Every request goes through `client`, which
 * is bound to this project's session id.
 */
function ResearchWorkspace({ client, switcher, onBusyChange, onSessionData, demo = false, onExitDemo, projectTitle = null }) {
  const research = useResearch(client, { demo })
  const { graph, workspace, impact, checks, health, source, status, setImpact } = research
  const sessionId = client?.sessionId ?? null
  const live = source === 'live'
  const chatEnabled = live && !!workspace?.question
  const chat = useChat(client, { enabled: chatEnabled })
  const { addNote } = chat
  const onUploadSettled = useCallback((item) => {
    if (item.status === STATUS.done && item.mode === 'evaluate' && item.result?.impacts) setImpact(item.result)
    const note = uploadNote(item)
    addNote(note.text, { tone: note.tone, forSession: item.sessionId })
  }, [addNote, setImpact])
  const uploads = useUploads({ client, refresh: research.refresh, onSettled: onUploadSettled })
  const reanalysis = useReanalysis({ client, refresh: research.refresh })
  const busy = describeBusy({ uploads, reanalysis, chat })
  const busyKey = busy?.label ?? ''
  useEffect(() => {
    onBusyChange?.(busy)
  }, [busyKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onBusyChange?.(null), []) // eslint-disable-line react-hooks/exhaustive-deps
  // Keep the project list (titles follow the confirmed question; paper counts) in step with this project.
  const dataKey = live ? `${workspace?.question}|${workspace?.counts?.papers}|${workspace?.counts?.claims}|${workspace?.counts?.pending_papers}` : ''
  useEffect(() => {
    if (dataKey) onSessionData?.()
  }, [dataKey]) // eslint-disable-line react-hooks/exhaustive-deps
  const pendingPapers = workspace?.pending_papers ?? []
  const [questionOpen, setQuestionOpen] = useState(false)
  const autoOpened = useRef(false)
  const canEditQuestion = live && !reanalysis.running && !uploads.busy
  // First visit to an empty workspace: open the question dialog once (the user can still close it).
  useEffect(() => {
    if (live && workspace && !workspace.question && !autoOpened.current) {
      autoOpened.current = true
      setQuestionOpen(true)
    }
  }, [live, workspace])
  // Called only after the server confirmed the save. A failed refresh throws back to the dialog, which
  // reports "saved, but the view was not updated" and offers a retry (it never re-sends the question).
  const onQuestionConfirmed = useCallback(async (ws) => {
    await research.refresh({ fallback: false })
    setQuestionOpen(false)
    const pending = ws?.pending_papers?.length ?? 0
    addNote(ws?.analysis_cleared
      ? `Central question changed. The earlier analysis was cleared; ${pending} ${pending === 1 ? 'paper awaits' : 'papers await'} re-analysis (start it in the workspace panel).`
      : ws?.reset ? 'Central question set. The earlier project was deleted, as you chose.' : 'Central question set.',
    { tone: 'ok', forSession: sessionId })
  }, [research.refresh, addNote, sessionId]) // eslint-disable-line react-hooks/exhaustive-deps
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
    : !workspace?.question ? 'Set a research question before uploading papers.'
      : pendingPapers.length || reanalysis.running ? 'Re-analyze the existing papers under the new question first.' : ''
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
        onEditQuestion={live ? () => setQuestionOpen(true) : undefined}
        canEditQuestion={canEditQuestion}
        reanalysis={live ? reanalysis : null}
        collapsed={collapsed}
        onToggle={() => setManualCollapsed(!collapsed)}
        switcher={switcher}
      />
      <main className="main">
        <TopBar
          graph={graph} workspace={workspace} checks={checks} health={health} source={source}
          query={query} onQuery={setQuery} onPick={pickSearch} filters={filters} onFilters={setFilters}
          assistantOpen={assistantOpen} onToggleAssistant={toggleAssistant} projectTitle={projectTitle}
        />
        <DataBanner source={source} error={status === 'error' ? research.error : null} onRetry={() => research.refresh()}
          onExitDemo={onExitDemo} />
        <section className="stage" aria-label="Evidence graph">
          {status === 'loading' && <div className="stage-note">Loading evidence graph…</div>}
          {graph && (
            <GraphCanvas
              graph={graph} hidden={hidden} matches={matches} selection={selection} highlight={highlight}
              onSelect={setSelection}
            />
          )}
          {live && workspace && !workspace.question && !questionOpen && (
            <div className="onboard-card" role="region" aria-label="Start a research workspace">
              <div className="q-kicker">Start a research workspace</div>
              <p>Describe what you want to investigate. Gemini can suggest a focused central question; you review, edit and confirm it.</p>
              <button type="button" className="btn btn-sm btn-primary" onClick={() => setQuestionOpen(true)}>Set the central question</button>
            </div>
          )}
          {graph && <Legend />}
          {graph && (
            <Inspector
              graph={graph} workspace={workspace} selection={selection} refs={refs}
              onNavigate={navigate} onClose={() => setSelection(null)} onAsk={chatEnabled ? askAbout : undefined}
              onEditQuestion={canEditQuestion ? () => setQuestionOpen(true) : undefined}
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
        canUpload={live && !!workspace?.question && !pendingPapers.length && !reanalysis.running}
        uploadBlockedReason={uploadBlockedReason} currentQuestion={workspace?.question ?? null}
        hasLibrary={!!workspace?.has_library} chatEnabled={chatEnabled} chatBlockedReason={chatBlockedReason}
        context={chatContext} contextLabel={contextLabel} onClearContext={() => setChatContext(null)}
        onReference={onReference} focusTick={focusTick}
      />
      {client && (
        <QuestionDialog open={questionOpen} client={client} workspace={workspace} onClose={() => setQuestionOpen(false)}
          onConfirmed={onQuestionConfirmed} />
      )}
    </div>
  )
}

function NoProjects({ onCreate, switcher }) {
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      await onCreate()
    } catch (err) {
      setError(err?.message ?? 'The project could not be created.')
      setBusy(false)
    }
  }
  return (
    <div className="app app-empty">
      <aside className="sidebar" aria-label="Research workspace">
        <div className="sb-head">
          <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
          <span className="sb-brand">ResearchLens</span>
        </div>
        <div className="sb-body">{switcher}</div>
      </aside>
      <main className="main">
        <section className="stage" aria-label="Start">
          <div className="onboard-card" role="region" aria-label="Create a research project">
            <div className="q-kicker">Welcome to ResearchLens</div>
            <p>Each research project has its own central question, papers, evidence graph and assistant conversation.</p>
            {error && <div className="warn-card" role="alert">{error}</div>}
            <button type="button" className="btn btn-sm btn-primary" onClick={create} disabled={busy}>
              {busy ? 'Creating…' : 'Create your first research project'}
            </button>
          </div>
        </section>
      </main>
    </div>
  )
}

/** The server could not be reached, or it needs maintenance. Never shows sample data in place of real research. */
function ConnectionProblem({ maintenance, error, onRetry, onDemo }) {
  const [retrying, setRetrying] = useState(false)
  const retry = async () => {
    setRetrying(true)
    try {
      await onRetry()
    } finally {
      setRetrying(false)
    }
  }
  return (
    <div className="app app-empty">
      <aside className="sidebar" aria-label="Research workspace">
        <div className="sb-head">
          <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
          <span className="sb-brand">ResearchLens</span>
        </div>
      </aside>
      <main className="main">
        <section className="stage" aria-label="Connection status">
          <div className="onboard-card conn-card" role="alert">
            {maintenance ? (
              <>
                <div className="q-kicker">Maintenance required</div>
                <h2>ResearchLens needs a one-time database upgrade</h2>
                <p>
                  The server is running, but its database still uses the single-project layout. Your research has not been
                  changed. An administrator must stop the backend, run <code>python -m app.migrate --backup-dir &lt;dir&gt;</code>
                  {' '}(it makes a verified backup first), and start the backend again.
                </p>
              </>
            ) : (
              <>
                <div className="q-kicker">Connection problem</div>
                <h2>Unable to connect to ResearchLens</h2>
                <p>
                  The server is not responding. Your projects are stored on the server and are not shown until it can be
                  reached — nothing is substituted for them.
                </p>
                {error?.message && <p className="conn-detail">Details: {error.message}</p>}
              </>
            )}
            <div className="conn-actions">
              <button type="button" className="btn btn-sm btn-primary" onClick={retry} disabled={retrying}>
                {retrying ? 'Retrying…' : 'Retry connection'}
              </button>
              <button type="button" className="link-btn muted" onClick={onDemo}>Explore fictional demo data instead</button>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}

const DEMO_PARAM = 'demo'

function initialDemo() {
  try {
    return new URLSearchParams(window.location.search).get(DEMO_PARAM) === 'sample'
  } catch {
    return false
  }
}

export default function App() {
  const sessions = useSessions()
  const [busy, setBusy] = useState(null)
  const [demo, setDemo] = useState(initialDemo) // fictional sample data ONLY when explicitly chosen
  const { activeId, reload } = sessions
  const client = useMemo(() => (activeId ? sessionApi(activeId) : null), [activeId])
  const onSessionData = useCallback(() => { reload() }, [reload])
  const exitDemo = () => {
    setDemo(false)
    try {
      const url = new URL(window.location.href)
      url.searchParams.delete(DEMO_PARAM)
      window.history.replaceState(null, '', url)
    } catch {
      /* ignore */
    }
    reload()
  }

  if (demo) {
    return <ResearchWorkspace key="demo" client={null} demo onExitDemo={exitDemo} switcher={null} />
  }
  if (sessions.status === 'loading') return <div className="app-loading">Loading research projects…</div>
  if (sessions.status === 'error') {
    return <ConnectionProblem maintenance={sessions.maintenance} error={sessions.error} onRetry={() => reload()}
      onDemo={() => setDemo(true)} />
  }
  const switcher = (
    <SessionSwitcher sessions={sessions.sessions} active={sessions.active} busy={busy}
      onSelect={sessions.select} onCreate={() => sessions.create()} onRename={sessions.rename} onDelete={sessions.remove} />
  )
  if (!sessions.sessions.length || !client) {
    return <NoProjects onCreate={() => sessions.create()} switcher={switcher} />
  }
  return (
    <ResearchWorkspace key={activeId} client={client} switcher={switcher} onBusyChange={setBusy}
      onSessionData={onSessionData} projectTitle={sessions.active?.title ?? null} />
  )
}
