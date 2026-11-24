import { useState } from 'react'
import { describeExtraction } from '../../graph/extraction.js'
import { claimCounts } from '../../graph/toElements.js'
import ConfirmDialog from '../ConfirmDialog.jsx'

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`
const CLEAR_BLOCKED = 'Not available while papers are being uploaded or analyzed, or while offline.'

// Left research workspace (BioOKF sidebar language): the central question (set / change through the
// question dialog), deliberate re-analysis after a confirmed question change, and the papers.
function PaperList({ graph, selection, refs, onSelectPaper, onRemove = null, canRemove = false }) {
  const papers = Object.values(graph?.papers ?? {})
  const counts = claimCounts(graph)
  if (!papers.length) return <p className="empty-note">No papers yet.</p>
  const ordered = [...papers.filter((p) => !p.is_new), ...papers.filter((p) => p.is_new)]
  return (
    <ul className="paper-list">
      {ordered.map((p) => {
        const active = selection?.kind === 'paper' && selection.id === p.id
        const n = counts[`paper:${p.id}`] ?? 0
        const outcome = describeExtraction(p.extraction)
        return (
          <li key={p.id} className="paper-item">
            <button type="button" className={`paper-card ${active ? 'active' : ''}`} onClick={() => onSelectPaper(p.id)}>
              <span className="paper-title">
                <span className={`node-dot ${p.access_level === 'abstract' ? 'dashed' : ''}`}
                  style={{ background: p.access_level === 'abstract' ? '#8A86C4' : '#6366A8', boxShadow: p.is_new ? '0 0 0 2px #0ea5e9' : undefined }} />
                <span className="paper-title-text">{p.title}</span>
                {refs?.[`paper:${p.id}`] != null && <span className="node-chip paper">Node {refs[`paper:${p.id}`]}</span>}
              </span>
              <span className="paper-meta">
                {n} {n === 1 ? 'claim' : 'claims'} · {p.access_level === 'abstract' ? 'abstract only' : 'full text'}
                {p.year ? ` · ${p.year}` : ''}
              </span>
              {(p.is_new || p.access_level === 'abstract' || p.analysis_status === 'pending' || outcome) && (
                <span className="badges">
                  {outcome && p.analysis_status !== 'pending' && (
                    <span className="badge badge-pending" title={outcome.title}>{outcome.badge}</span>
                  )}
                  {p.analysis_status === 'pending' && <span className="badge badge-pending">Awaiting analysis</span>}
                  {p.is_new && <span className="badge badge-new">Evaluated</span>}
                  {p.access_level === 'abstract' && <span className="badge">Abstract only</span>}
                </span>
              )}
            </button>
            {onRemove && (
              <button type="button" className="icon-btn paper-remove" disabled={!canRemove}
                aria-label={`Remove “${p.title}” from this project`}
                title={canRemove ? 'Remove this paper from the project' : CLEAR_BLOCKED}
                onClick={() => onRemove({ ...p, claims: n })}>
                <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" /></svg>
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function ReanalysisCard({ pending, reanalysis, canRun }) {
  const n = pending.length
  if (!n && !reanalysis.running) return null
  return (
    <div className="reanalysis-card" role="status" data-testid="reanalysis">
      {reanalysis.running ? (
        <>
          <div className="ra-title"><span className="spinner" aria-hidden="true" /> Re-analyzing {reanalysis.done + 1} of {reanalysis.total}</div>
          <div className="ra-text">{reanalysis.current?.title} — extracting claims, comparing{reanalysis.current?.is_new ? ', assessing impact' : ''}.</div>
        </>
      ) : (
        <>
          <div className="ra-title">{n} {n === 1 ? 'paper awaits' : 'papers await'} analysis under the new question</div>
          <div className="ra-text">
            Their text is stored. Claims, relationships and impacts are extracted and judged again only when you start it
            (Gemini calls, usually 20–80 s per paper). Uploads are paused until then.
          </div>
          {reanalysis.error && (
            <div className="ra-error" role="alert">“{reanalysis.error.paper.title}” was not analyzed: {reanalysis.error.message}</div>
          )}
          <button type="button" className="btn btn-sm" disabled={!canRun} onClick={() => reanalysis.start(pending)}>
            {reanalysis.error || reanalysis.done ? 'Continue re-analysis' : `Re-analyze ${n} ${n === 1 ? 'paper' : 'papers'}`}
          </button>
        </>
      )}
    </div>
  )
}

export default function WorkspacePanel({
  graph, workspace, selection, refs, onSelectPaper, onSelectQuestion, onEditQuestion, canEditQuestion, reanalysis,
  collapsed, onToggle, switcher = null, onClearPapers = null, onRemovePaper = null, canClear = false, projectTitle = null,
}) {
  const pending = workspace?.pending_papers ?? []
  const [confirm, setConfirm] = useState(null) // { kind: 'all' } | { kind: 'one', paper } — frozen when opened
  const nPapers = Object.keys(graph?.papers ?? {}).length
  const c = workspace?.counts ?? {}
  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`} aria-label="Research workspace">
      <div className="sb-head">
        <img className="brand-mark" src="/logo.svg" alt="" aria-hidden="true" />
        <span className="sb-brand">SpiderMonkey</span>
        <button type="button" className="icon-btn" onClick={onToggle} aria-label={collapsed ? 'Expand workspace' : 'Collapse workspace'}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d={collapsed ? 'M6 3.5 10.5 8 6 12.5' : 'M10 3.5 5.5 8 10 12.5'} /></svg>
        </button>
      </div>
      {!collapsed && (
        <div className="sb-body">
          {switcher}
          <div className="sb-section">Research question</div>
          <div className="question-wrap">
            <button type="button" className={`question-card ${selection?.kind === 'question' ? 'active' : ''}`}
              onClick={onSelectQuestion} title="Show the Central Question in the graph">
              <div className="q-kicker">Central Question</div>
              <div className="q-text">{workspace?.question || 'No research question set'}</div>
              <div className="q-meta">
                {workspace?.counts?.library_papers ?? 0} library papers · {workspace?.counts?.claims ?? 0} claims
              </div>
              {workspace?.source === 'default' && <div className="q-hint">Demo default from .env</div>}
            </button>
            {onEditQuestion && (
              <button type="button" className={workspace?.question ? 'link-btn q-edit' : 'btn btn-sm q-set'}
                disabled={!canEditQuestion} onClick={onEditQuestion}
                title={canEditQuestion ? undefined : 'Not available while papers are being analyzed or offline.'}>
                {workspace?.question ? 'Change Question' : 'Set the central question'}
              </button>
            )}
          </div>
          {reanalysis && <ReanalysisCard pending={pending} reanalysis={reanalysis} canRun={!!canEditQuestion} />}

          <div className="sb-section sb-section-row">
            Papers
            {onClearPapers && nPapers > 0 && (
              <button type="button" className="link-btn danger-link" disabled={!canClear}
                title={canClear ? 'Permanently delete every paper in this project' : CLEAR_BLOCKED}
                onClick={() => setConfirm({ kind: 'all' })}>
                Clear All Papers
              </button>
            )}
          </div>
          <PaperList graph={graph} selection={selection} refs={refs} onSelectPaper={onSelectPaper}
            onRemove={onRemovePaper ? (paper) => setConfirm({ kind: 'one', paper }) : null} canRemove={canClear} />
        </div>
      )}
      {confirm?.kind === 'all' && (
        <ConfirmDialog title="Clear all papers in this project?" project={projectTitle} confirmLabel="Clear All Papers"
          busyLabel="Clearing…" onCancel={() => setConfirm(null)}
          onConfirm={async () => { await onClearPapers(); setConfirm(null) }}>
          <p>
            This permanently deletes {plural(c.papers ?? nPapers, 'paper')} and their text, {plural(c.claims ?? 0, 'claim')},
            {' '}{plural(c.relationships ?? 0, 'relationship')}, impact results, reviews and Node numbers,
            {' '}<b>and this project&apos;s chat</b> (its quotes come from these papers).
          </p>
          <p>The central question is kept. Other projects are not affected. It cannot be undone.</p>
        </ConfirmDialog>
      )}
      {confirm?.kind === 'one' && (
        <ConfirmDialog title="Remove this paper?" project={projectTitle} confirmLabel="Remove paper" busyLabel="Removing…"
          onCancel={() => setConfirm(null)}
          onConfirm={async () => { await onRemovePaper(confirm.paper); setConfirm(null) }}>
          <p className="qd-current"><span>Paper:</span> {confirm.paper.title}</p>
          <p>
            This permanently deletes the paper, its text, {plural(confirm.paper.claims ?? 0, 'claim')} and every relationship,
            impact result and review that involves them{confirm.paper.is_new ? ', including its impact analysis' : ''}.
          </p>
          <p>
            The chat is kept. Answers that used this paper are marked “Evidence removed”, its quotes are no longer shown,
            and they are not used as context for new answers. It cannot be undone.
          </p>
        </ConfirmDialog>
      )}
    </aside>
  )
}
