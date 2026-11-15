import { useEffect, useMemo, useRef, useState } from 'react'
import { searchNodes } from '../graph/toElements.js'

// Top-bar search (⌘K): live-dims non-matching nodes; dropdown ranks starts-with before contains.
export default function SearchBox({ graph, query, onQuery, onPick }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const inputRef = useRef(null)
  const results = useMemo(() => searchNodes(graph, query), [graph, query])

  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const pick = (hit) => {
    if (!hit) return
    onPick(hit)
    setOpen(false)
    inputRef.current?.blur()
  }

  return (
    <div className="search">
      <svg className="search-icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5 14 14" /></svg>
      <input
        ref={inputRef}
        type="search"
        placeholder="Search claims, papers…"
        aria-label="Search claims and papers"
        value={query}
        onChange={(e) => { onQuery(e.target.value); setOpen(true); setActive(0) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)) }
          if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
          if (e.key === 'Enter') pick(results[active])
          if (e.key === 'Escape') { onQuery(''); inputRef.current?.blur() }
        }}
      />
      <kbd>⌘K</kbd>
      {open && query.trim() && (
        <ul className="search-results" role="listbox">
          {results.length === 0 && <li className="empty">No matching claims or papers</li>}
          {results.map((r, i) => (
            <li
              key={r.id}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : ''}
              onMouseDown={(e) => { e.preventDefault(); pick(r) }}
              onMouseEnter={() => setActive(i)}
            >
              <span className={`kind-dot ${r.type}`} />
              <span className="kind">{r.type}</span>
              <span className="text">{r.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
