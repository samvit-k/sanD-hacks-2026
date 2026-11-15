import { claimCounts } from '../../graph/toElements.js'

// Left research workspace (BioOKF sidebar language). Shell for the visual checkpoint:
// research question + uploaded papers. Upload and evaluation controls are added in the next stage.
function PaperList({ graph, selection, onSelectPaper }) {
  const papers = Object.values(graph?.papers ?? {})
  const counts = claimCounts(graph)
  if (!papers.length) return <p className="empty-note">No papers yet.</p>
  const ordered = [...papers.filter((p) => !p.is_new), ...papers.filter((p) => p.is_new)]
  return (
    <ul className="paper-list">
      {ordered.map((p) => {
        const active = selection?.kind === 'paper' && selection.id === p.id
        const n = counts[`paper:${p.id}`] ?? 0
        return (
          <li key={p.id}>
            <button type="button" className={`paper-card ${active ? 'active' : ''}`} onClick={() => onSelectPaper(p.id)}>
              <span className="paper-title">
                <span className={`node-dot ${p.access_level === 'abstract' ? 'dashed' : ''}`}
                  style={{ background: p.access_level === 'abstract' ? '#8A86C4' : '#6366A8', boxShadow: p.is_new ? '0 0 0 2px #0ea5e9' : undefined }} />
                {p.title}
              </span>
              <span className="paper-meta">
                {n} {n === 1 ? 'claim' : 'claims'} · {p.access_level === 'abstract' ? 'abstract only' : 'full text'}
                {p.year ? ` · ${p.year}` : ''}
              </span>
              {(p.is_new || p.access_level === 'abstract') && (
                <span className="badges">
                  {p.is_new && <span className="badge badge-new">Evaluated</span>}
                  {p.access_level === 'abstract' && <span className="badge">Abstract only</span>}
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export default function WorkspacePanel({ graph, workspace, selection, onSelectPaper, collapsed, onToggle }) {
  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`} aria-label="Research workspace">
      <div className="sb-head">
        <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
        <span className="sb-brand">ResearchLens</span>
        <button type="button" className="icon-btn" onClick={onToggle} aria-label={collapsed ? 'Expand workspace' : 'Collapse workspace'}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d={collapsed ? 'M6 3.5 10.5 8 6 12.5' : 'M10 3.5 5.5 8 10 12.5'} /></svg>
        </button>
      </div>
      {!collapsed && (
        <div className="sb-body">
          <div className="sb-section">Research question</div>
          <div className="question-card">
            <div className="q-text">{workspace?.question || 'No research question set'}</div>
            <div className="q-meta">
              {workspace?.counts?.library_papers ?? 0} library papers · {workspace?.counts?.claims ?? 0} claims
            </div>
            {workspace?.source === 'default' && <div className="q-hint">Demo default from .env</div>}
          </div>

          <div className="sb-section">Papers</div>
          <PaperList graph={graph} selection={selection} onSelectPaper={onSelectPaper} />
        </div>
      )}
    </aside>
  )
}
