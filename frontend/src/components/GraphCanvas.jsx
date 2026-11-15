import cytoscape from 'cytoscape'
import { useCallback, useEffect, useRef, useState } from 'react'
import { computeLayout } from '../graph/layout.js'
import { buildStyle } from '../graph/style.js'
import { buildElements } from '../graph/toElements.js'
import ZoomControls from './ZoomControls.jsx'

const DOT_SPACING = 22
const LABEL_ZOOM = 0.6 // below this, claim labels show only on hover/selection/search (BioOKF hub-only labels)
const LEGEND_NUDGE = { x: 110, y: -30 } // initial view shifts away from the bottom-left legend card

/** Map an app selection ({kind, id}) to a Cytoscape element id. */
export function elementIdFor(selection) {
  if (!selection) return null
  switch (selection.kind) {
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
  if (d.type === 'paper') return { kind: 'paper', id: d.paper_id }
  if (d.type === 'claim') return { kind: 'claim', id: d.claim_id }
  if (d.type === 'discovery') return { kind: 'discovery', id: d.index }
  return null
}

export default function GraphCanvas({ graph, hidden, matches, selection, highlight, onSelect }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)
  const posRef = useRef({})
  const onSelectRef = useRef(onSelect)
  const [zoom, setZoom] = useState(1)
  onSelectRef.current = onSelect

  const updateLabels = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    const z = cy.zoom()
    cy.batch(() => {
      cy.nodes('.claim-node').forEach((n) => {
        n.toggleClass('labelled', z >= LABEL_ZOOM || n.hasClass('hover') || n.selected() || n.hasClass('match') || n.hasClass('nbr'))
      })
    })
  }, [])

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
        updateLabels()
      })
    }
    cy.on('viewport', onViewport)
    // Keep Cytoscape's canvas in sync when the container resizes (e.g. sidebar collapse/expand).
    const resizer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => cy.resize()) : null
    resizer?.observe(containerRef.current)
    cy.on('tap', (evt) => {
      if (evt.target === cy) onSelectRef.current(null)
    })
    cy.on('tap', 'node, edge.evidence', (evt) => {
      onSelectRef.current(selectionFor(evt.target))
    })
    cy.on('mouseover', 'node, edge.evidence', (evt) => {
      evt.target.addClass('hover')
      if (containerRef.current) containerRef.current.style.cursor = 'pointer'
      updateLabels()
    })
    cy.on('mouseout', 'node, edge.evidence', (evt) => {
      evt.target.removeClass('hover')
      if (containerRef.current) containerRef.current.style.cursor = 'default'
      updateLabels()
    })
    return () => {
      cancelAnimationFrame(frame)
      resizer?.disconnect()
      cy.destroy()
      cyRef.current = null
    }
  }, [updateLabels])

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
    updateLabels()
  }, [graph, updateLabels])

  // Filters.
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.batch(() => cy.elements().forEach((e) => e.toggleClass('hidden', hidden?.has(e.id()) ?? false)))
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
      cy.animate({ center: { eles: sel }, zoom: Math.max(cy.zoom(), 1.05) }, { duration: 380, easing: 'ease-in-out-cubic' })
    }
    updateLabels()
  }, [graph, selection, matches, highlight, updateLabels])

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
      <ZoomControls zoom={zoom} onZoomIn={() => zoomBy(1.25)} onZoomOut={() => zoomBy(0.8)} onFit={fit} onRelayout={relayout} />
    </div>
  )
}
