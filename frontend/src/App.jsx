import { useEffect, useMemo, useState } from 'react'
import GraphCanvas from './components/GraphCanvas.jsx'
import Legend from './components/Legend.jsx'
import TopBar from './components/TopBar.jsx'
import WorkspacePanel from './components/workspace/WorkspacePanel.jsx'
import { DEFAULT_FILTERS, hiddenIds, matchingIds } from './graph/toElements.js'
import { useResearch } from './state/useResearch.js'

const NARROW_QUERY = '(max-width: 1100px)'

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
  const { graph, workspace, impact, checks, health, source, status } = research
  const [selection, setSelection] = useState(null)
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const narrow = useNarrow(NARROW_QUERY)
  const [manualCollapsed, setManualCollapsed] = useState(null) // null = follow the viewport
  const collapsed = manualCollapsed ?? narrow
  useEffect(() => setManualCollapsed(null), [narrow]) // crossing the breakpoint re-applies the default

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

  const pickSearch = (hit) => {
    const node = graph.nodes.find((n) => n.data.id === hit.id)?.data
    if (!node) return
    if (node.type === 'paper') setSelection({ kind: 'paper', id: node.paper_id, focus: true })
    else if (node.type === 'claim') setSelection({ kind: 'claim', id: node.claim_id, focus: true })
    else if (node.type === 'discovery') setSelection({ kind: 'discovery', id: node.index, focus: true })
  }

  return (
    <div className={`app ${collapsed ? 'sb-collapsed' : ''}`}>
      <WorkspacePanel
        graph={graph}
        workspace={workspace}
        selection={selection}
        onSelectPaper={(id) => setSelection({ kind: 'paper', id, focus: true })}
        collapsed={collapsed}
        onToggle={() => setManualCollapsed(!collapsed)}
      />
      <main className="main">
        <TopBar
          graph={graph} workspace={workspace} checks={checks} health={health} source={source}
          query={query} onQuery={setQuery} onPick={pickSearch} filters={filters} onFilters={setFilters}
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
        </section>
      </main>
    </div>
  )
}
