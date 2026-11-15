// Research-project switcher at the top of the left sidebar: list, switch, create, rename, delete.
// - Leaving a project while work runs there (switching OR creating a new project) always needs an explicit
//   "Switch anyway", with an exact statement of what continues and what stops (see `busy` from the workspace).
// - Deleting opens a separate dialog bound to the project chosen at that moment (frozen), with an explicit
//   acknowledgement; it can never end up deleting a different project.
import { useEffect, useRef, useState } from 'react'

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`

function DeleteDialog({ session, onCancel, onConfirm }) {
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const c = session.counts ?? {}
  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(session.id)
    } catch (err) {
      setError(err?.message ?? 'The project was not deleted.')
      setBusy(false)
    }
  }
  return (
    <div className="qd-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onCancel() }}>
      <div className="qd qd-small" role="alertdialog" aria-modal="true" aria-labelledby="del-title">
        <div className="qd-head"><h2 id="del-title">Delete this research project?</h2></div>
        <div className="qd-body">
          <p className="qd-current"><span>Project:</span> {session.title}</p>
          <div className="qd-danger">
            <p>
              This permanently deletes the project&apos;s central question, {plural(c.papers ?? 0, 'paper')} and their text,
              {' '}{plural(c.claims ?? 0, 'claim')}, {plural(c.relationships ?? 0, 'relationship')}, its Node numbers and
              {' '}{plural(c.chat_messages ?? 0, 'chat message')}. It cannot be undone. Other projects are not affected.
            </p>
            <label className="del-ack">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} disabled={busy} />
              I understand this project and its research will be permanently deleted.
            </label>
          </div>
          {error && <div className="warn-card" role="alert">{error}</div>}
        </div>
        <div className="qd-foot">
          <button type="button" className="btn btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-sm btn-danger" disabled={!ack || busy} onClick={run}>
            {busy ? 'Deleting…' : 'Delete project permanently'}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * `busy`: null, or { label, detail } describing work running in the ACTIVE project, e.g.
 * { label: 'A re-analysis', detail: 'The paper being analyzed now finishes on the server; …' }.
 */
export default function SessionSwitcher({ sessions, active, busy = null, onSelect, onCreate, onRename, onDelete, disabled }) {
  const [open, setOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [title, setTitle] = useState('')
  const [leaveTo, setLeaveTo] = useState(null) // { kind: 'select', id } | { kind: 'create' } awaiting "Switch anyway"
  const [deleteTarget, setDeleteTarget] = useState(null) // frozen copy of the project chosen for deletion
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const rootRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (!rootRef.current?.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  useEffect(() => { if (!open) { setLeaveTo(null); setError(null) } }, [open])

  // --- leaving the current project (explicitly confirmed while work is running) ---
  const doSelect = (id) => {
    onSelect(id)
    setOpen(false)
  }
  const doCreate = async () => {
    setCreating(true)
    setError(null)
    try {
      await onCreate()
      setOpen(false)
    } catch (err) {
      setError(err?.message ?? 'The project could not be created.')
    } finally {
      setCreating(false)
    }
  }
  const requestSelect = (id) => {
    if (id === active?.id) return setOpen(false)
    if (busy) return setLeaveTo({ kind: 'select', id })
    return doSelect(id)
  }
  const requestCreate = () => {
    if (busy) return setLeaveTo({ kind: 'create' }) // always asks while work runs, whatever was pending before
    return doCreate()
  }
  const confirmLeave = () => {
    const target = leaveTo
    setLeaveTo(null)
    if (target?.kind === 'create') doCreate()
    else if (target?.kind === 'select') doSelect(target.id)
  }

  const saveTitle = async (value) => {
    setError(null)
    try {
      await onRename(active.id, value)
      setRenaming(false)
    } catch (err) {
      setError(err?.message ?? 'The project was not renamed.')
    }
  }

  return (
    <div className="sess" ref={rootRef}>
      {renaming && active ? (
        <form className="sess-rename" onSubmit={(e) => { e.preventDefault(); if (title.trim()) saveTitle(title) }}>
          <input aria-label="Project title" value={title} maxLength={120} autoFocus
            onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape') setRenaming(false) }} />
          <div className="sess-rename-actions">
            <button type="submit" className="btn btn-sm btn-primary" disabled={!title.trim()}>Save</button>
            <button type="button" className="btn btn-sm" onClick={() => setRenaming(false)}>Cancel</button>
            {active.title_source === 'manual' && (
              <button type="button" className="link-btn muted" onClick={() => saveTitle(null)}>Use automatic title</button>
            )}
          </div>
          {error && <div className="sess-error" role="alert">{error}</div>}
        </form>
      ) : (
        <button type="button" className="sess-current" aria-haspopup="true" aria-expanded={open} disabled={disabled}
          onClick={() => setOpen(!open)} data-testid="session-switcher">
          <span className="sess-kicker">Research project</span>
          <span className="sess-title">{active?.title ?? (sessions.length ? 'Select a project' : 'No projects yet')}</span>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 6.5 8 10l3.5-3.5" /></svg>
        </button>
      )}
      {open && (
        <div className="sess-menu" role="menu" aria-label="Research projects">
          <ul className="sess-list">
            {sessions.map((s) => (
              <li key={s.id}>
                <button type="button" role="menuitemradio" aria-checked={s.id === active?.id}
                  className={`sess-item ${s.id === active?.id ? 'on' : ''}`} onClick={() => requestSelect(s.id)}>
                  <span className="sess-item-title">{s.title}</span>
                  <span className="sess-item-meta">
                    {s.question ? plural(s.counts?.papers ?? 0, 'paper') : 'No central question yet'}
                    {s.counts?.pending_papers ? ` · ${s.counts.pending_papers} awaiting analysis` : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {leaveTo && busy && (
            <div className="sess-warn" role="alert">
              <b>{busy.label} is running in this project.</b> {busy.detail}
              <div className="sess-warn-actions">
                <button type="button" className="btn btn-sm" onClick={() => setLeaveTo(null)}>Stay</button>
                <button type="button" className="btn btn-sm btn-primary" onClick={confirmLeave}>Switch anyway</button>
              </div>
            </div>
          )}
          {error && <div className="sess-error" role="alert">{error}</div>}
          <div className="sess-actions">
            <button type="button" className="sess-action" onClick={requestCreate} disabled={creating}>
              {creating ? 'Creating…' : '+ New research project'}
            </button>
            {active && (
              <>
                <button type="button" className="sess-action" onClick={() => { setTitle(active.title); setRenaming(true); setOpen(false) }}>
                  Rename project…
                </button>
                <button type="button" className="sess-action danger" disabled={!!busy}
                  title={busy ? `${busy.label} is running in this project; wait for it to finish before deleting.` : undefined}
                  onClick={() => { setDeleteTarget({ ...active }); setOpen(false) }}>
                  Delete project…
                </button>
              </>
            )}
          </div>
        </div>
      )}
      {deleteTarget && (
        <DeleteDialog session={deleteTarget} onCancel={() => setDeleteTarget(null)}
          onConfirm={async (id) => { await onDelete(id); setDeleteTarget(null) }} />
      )}
    </div>
  )
}
