import cytoscape from 'cytoscape'
import { useEffect, useRef, useState } from 'react'
import { computeLayout } from '../graph/layout.js'
import { buildStyle } from '../graph/style.js'
import { buildElements } from '../graph/toElements.js'
import { NODE_KINDS, RELATIONSHIPS } from '../graph/vocab.js'
import ZoomControls from './ZoomControls.jsx'

const DOT_SPACING = 22
const LEGEND_NUDGE = { x: 110, y: -30 } // initial view shifts away from the bottom-left legend card

/** Map an app selection ({kind, id}) to a Cytoscape element id. */
export function elementIdFor(selection) {
  if (!selection) return null
  switch (selection.kind) {
    case 'question': return 'q'
    case 'paper': return `paper:${selection.id}`
    case 'claim': return `claim:${selection.id}`
    case 'relationship': return `rel:${selection.id}`
    case 'discovery': return `disc:${selection.id}`
    default: return null
  }
}

function selectionFor(ele) {
  const d = ele.data()
  if (ele.isEdge()) return d.type === 'relationship' ? { kind: 'relationship', id: d.relationship_id } : null
  if (d.type === 'question') return { kind: 'question', id: 'q' }
  if (d.type === 'paper') return { kind: 'paper', id: d.paper_id }
  if (d.type === 'claim') return { kind: 'claim', id: d.claim_id }
  if (d.type === 'discovery') return { kind: 'discovery', id: d.index }
  return null
}

/** Short hover hint: element type + its short label (full text only appears on selection). */
function tipText(ele) {
  const d = ele.data()
  if (ele.isEdge()) {
    const rel = RELATIONSHIPS[d.label]
    return `${d.insufficient_evidence ? 'Insufficient evidence' : rel?.label ?? 'Relationship'} · click for evidence`
  }
  const kind = NODE_KINDS[d.kind]?.label ?? 'Node'
  return d.kind === 'question' ? `${kind} · click to read it` : `${kind} · ${d.displayLabel}`
}

export default function GraphCanvas({ graph, hidden, matches, selection, highlight, onSelect }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)
  const posRef = useRef({})
  const onSelectRef = useRef(onSelect)
  const [zoom, setZoom] = useState(1)
  const [tip, setTip] = useState(null)
  onSelectRef.current = onSelect

  // Create the Cytoscape instance once.
  useEffect(() => {
    const cy = cytoscape({
      container: containerRef.current,
      style: buildStyle(),
      minZoom: 0.2,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      boxSelectionEnabled: false,
      selectionType: 'single',
    })
    cyRef.current = cy
    let frame = 0
    const onViewport = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const z = cy.zoom()
        const { x, y } = cy.pan()
        const el = containerRef.current
        if (el) {
          el.style.backgroundSize = `${DOT_SPACING * z}px ${DOT_SPACING * z}px`
          el.style.backgroundPosition = `${x}px ${y}px`
        }
        setZoom(z)
        setTip(null)
      })
    }
    cy.on('viewport', onViewport)
    // Keep Cytoscape's canvas in sync when the container resizes (e.g. sidebar collapse/expand).
    const resizer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => cy.resize()) : null
    resizer?.observe(containerRef.current)
    cy.on('tap', (evt) => {
      if (evt.target === cy) onSelectRef.current(null)
    })
    cy.on('tap', 'node, edge.evidence, edge.evidence-hit', (evt) => {
      setTip(null)
      onSelectRef.current(selectionFor(evt.target))
    })
    // Hit edges stand in for their visible twin (hover styling + hint).
    const visual = (ele) => (ele.data('hitFor') ? cy.getElementById(ele.data('hitFor')) : ele)
    cy.on('mouseover', 'node, edge.evidence, edge.evidence-hit', (evt) => {
      const ele = visual(evt.target)
      ele.addClass('hover')
      if (containerRef.current) containerRef.current.style.cursor = 'pointer'
      const p = evt.renderedPosition ?? ele.renderedPosition?.()
      if (p) setTip({ x: p.x, y: p.y, text: tipText(ele) })
    })
    cy.on('mouseout', 'node, edge.evidence, edge.evidence-hit', (evt) => {
      visual(evt.target).removeClass('hover')
      if (containerRef.current) containerRef.current.style.cursor = 'default'
      setTip(null)
    })
    return () => {
      cancelAnimationFrame(frame)
      resizer?.disconnect()
      cy.destroy()
      cyRef.current = null
    }
  }, [])

  // Rebuild elements when the graph changes; existing nodes glide to their new positions.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const { nodes, edges } = buildElements(graph)
    const old = {}
    cy.nodes().forEach((n) => { old[n.id()] = { ...n.position() } })
    const pos = computeLayout(graph, posRef.current)
    posRef.current = pos
    const first = Object.keys(old).length === 0
    cy.batch(() => {
      cy.elements().remove()
      cy.add(nodes.map((n) => {
        const p = pos[n.data.id] ?? { x: 0, y: 0 }
        const side = n.data.kind === 'question' ? 'below' : p.x < -1 ? 'left' : 'right'
        return { ...n, data: { ...n.data, side }, position: { ...(old[n.data.id] ?? p) } }
      }))
      cy.add(edges)
    })
    if (first) {
      cy.fit(undefined, 90)
      if (cy.zoom() > 1.25) cy.zoom(1.25)
      cy.center()
      // The legend starts open only on wide screens (Legend.jsx); nudge the view away from it only then.
      if (!window.matchMedia?.('(max-width: 1100px)').matches) cy.panBy(LEGEND_NUDGE)
    } else {
      cy.nodes().forEach((n) => {
        const p = pos[n.id()]
        if (p) n.animate({ position: p }, { duration: 450, easing: 'ease-out-cubic' })
      })
    }
  }, [graph])

  // Filters.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => cy.elements().forEach((e) => e.toggleClass('hidden', hidden?.has(e.data('hitFor') ?? e.id()) ?? false)))
  }, [graph, hidden])

  // Selection, search matches, neighbourhood focus and impact highlights.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const selId = elementIdFor(selection)
    const sel = selId ? cy.getElementById(selId) : null
    cy.batch(() => {
      cy.elements().removeClass('faded nbr match')
      cy.elements(':selected').unselect()
      cy.nodes().removeClass(Object.keys({ challenges: 1, qualifies: 1, strengthens: 1, leaves_unresolved: 1 }).map((k) => `impact-${k}`).join(' '))
      for (const [nodeId, impact] of Object.entries(highlight ?? {})) cy.getElementById(nodeId).addClass(`impact-${impact}`)
      if (matches?.size) {
        cy.nodes().forEach((n) => {
          if (matches.has(n.id())) n.addClass('match')
          else if (n.id() !== 'q') n.addClass('faded')
        })
        cy.edges().forEach((e) => {
          if (!(matches.has(e.source().id()) && matches.has(e.target().id()))) e.addClass('faded')
        })
      }
      if (sel && sel.nonempty()) {
        sel.select()
        const keep = sel.isEdge() ? sel.union(sel.connectedNodes()) : sel.closedNeighborhood()
        keep.nodes().addClass('nbr')
        cy.elements().not(keep).not('#q').addClass('faded')
        keep.removeClass('faded')
      }
    })
    if (sel && sel.nonempty() && selection?.focus) {
      // Centre the selection in the canvas area NOT covered by the floating inspector card.
      const card = containerRef.current?.closest('.stage')?.querySelector('.inspector')
      const cardW = card ? card.offsetWidth + 16 : 0
      const sideBySide = cardW && cy.width() - cardW > 320 // narrow screens use a bottom sheet instead
      const visibleW = sideBySide ? cy.width() - cardW : cy.width()
      const visibleH = !sideBySide && card ? cy.height() - card.offsetHeight - 8 : cy.height()
      const bb = sel.boundingBox()
      // Zoom in to at least 1.05, but never so far that the selection (e.g. a long edge) leaves the view.
      const fitZoom = Math.min((visibleW - 80) / Math.max(bb.w, 1), (visibleH - 80) / Math.max(bb.h, 1))
      const zoom = Math.max(0.3, Math.min(Math.max(cy.zoom(), 1.05), fitZoom))
      const cx = (bb.x1 + bb.x2) / 2
      const cyy = (bb.y1 + bb.y2) / 2
      cy.animate({ zoom, pan: { x: visibleW / 2 - cx * zoom, y: visibleH / 2 - cyy * zoom } },
        { duration: 380, easing: 'ease-in-out-cubic' })
    }
  }, [graph, selection, matches, highlight])

  const zoomBy = (factor) => {
    const cy = cyRef.current
    if (!cy) return
    const center = { x: cy.width() / 2, y: cy.height() / 2 }
    cy.animate({ zoom: { level: cy.zoom() * factor, renderedPosition: center } }, { duration: 160 })
  }
  const fit = () => cyRef.current?.animate({ fit: { eles: cyRef.current.elements(':visible'), padding: 80 } }, { duration: 300 })
  const relayout = () => {
    posRef.current = {}
    const cy = cyRef.current
    if (!cy) return
    const pos = computeLayout(graph, {})
    posRef.current = pos
    cy.nodes().forEach((n) => pos[n.id()] && n.animate({ position: pos[n.id()] }, { duration: 450 }))
    setTimeout(fit, 480)
  }

  return (
    <div className="canvas-wrap">
      <div ref={containerRef} className="canvas" data-testid="graph-canvas" />
      {tip && <div className="graph-tip" style={{ left: tip.x + 12, top: tip.y + 12 }} role="tooltip">{tip.text}</div>}
      <ZoomControls zoom={zoom} onZoomIn={() => zoomBy(1.25)} onZoomOut={() => zoomBy(0.8)} onFit={fit} onRelayout={relayout} />
    </div>
  )
}
