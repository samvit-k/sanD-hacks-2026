import { useState } from 'react'
import { DEFAULT_FILTERS, relationshipCounts } from '../graph/toElements.js'
import { INSUFFICIENT, RELATIONSHIPS, RELATIONSHIP_ORDER } from '../graph/vocab.js'

// AGeneTic EdgeTypeFilter pattern: a checkbox + count per evidence type, plus view toggles.
export default function FilterPopover({ graph, filters, onChange }) {
  const [open, setOpen] = useState(false)
  const counts = relationshipCounts(graph)
  const off = RELATIONSHIP_ORDER.filter((k) => filters.labels[k] === false).length +
    (filters.insufficient ? 0 : 1) + (filters.structural ? 0 : 1) + (filters.discovery ? 0 : 1) + (filters.onlyNew ? 1 : 0)
  const hasNew = Object.values(graph?.papers ?? {}).some((p) => p.is_new)
  const hasLeads = (graph?.nodes ?? []).some((n) => n.data.type === 'discovery')
  const setLabel = (k, v) => onChange({ ...filters, labels: { ...filters.labels, [k]: v } })

  return (
    <div className="filter">
      <button type="button" className={`btn ${off ? 'btn-on' : ''}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 4h11M4.5 8h7M6.5 12h3" /></svg>
        Filter{off ? ` · ${off}` : ''}
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="popover filter-panel">
            <div className="pop-title">Evidence relationships</div>
            {RELATIONSHIP_ORDER.map((k) => (
              <label className="pop-row" key={k} title={RELATIONSHIPS[k].help}>
                <input type="checkbox" checked={filters.labels[k] !== false} onChange={(e) => setLabel(k, e.target.checked)} />
                <span className="swatch" style={{ background: RELATIONSHIPS[k].color }} />
                <span className="name">{RELATIONSHIPS[k].label}</span>
                <span className="count">{counts[k] ?? 0}</span>
              </label>
            ))}
            <label className="pop-row" title={INSUFFICIENT.help}>
              <input type="checkbox" checked={filters.insufficient} onChange={(e) => onChange({ ...filters, insufficient: e.target.checked })} />
              <span className="swatch faint" />
              <span className="name">{INSUFFICIENT.label}</span>
              <span className="count">{counts.insufficient}</span>
            </label>
            <div className="pop-sep" />
            <div className="pop-title">View</div>
            <label className="pop-row">
              <input type="checkbox" checked={filters.structural} onChange={(e) => onChange({ ...filters, structural: e.target.checked })} />
              <span className="name">Structural links (question → paper → claim)</span>
            </label>
            {hasNew && (
              <label className="pop-row">
                <input type="checkbox" checked={filters.onlyNew} onChange={(e) => onChange({ ...filters, onlyNew: e.target.checked })} />
                <span className="name">Only the evaluated paper’s relationships</span>
              </label>
            )}
            {hasLeads && (
              <label className="pop-row">
                <input type="checkbox" checked={filters.discovery} onChange={(e) => onChange({ ...filters, discovery: e.target.checked })} />
                <span className="name">Related research leads</span>
              </label>
            )}
            <button type="button" className="link-btn" onClick={() => onChange(DEFAULT_FILTERS)}>Reset filters</button>
          </div>
        </>
      )}
    </div>
  )
}