// Cytoscape stylesheet reproducing BioOKF Studio's canvas: small circular type-coloured nodes with a
// hairline ink border, labels to the right, thin straight edges, faint provenance (structural) edges.
// Note: Cytoscape ignores the alpha channel of rgba() colours, so transparency uses *-opacity properties.
import { IMPACTS, NODE_KINDS, RELATIONSHIPS } from './vocab.js'

const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif'
const INK = '#1d232a'
const DASH = { dashed: [5, 4], dotted: [1.5, 3] }

export function buildStyle() {
  const style = [
    {
      selector: 'node',
      style: {
        width: 'data(size)',
        height: 'data(size)',
        shape: 'ellipse',
        'background-color': '#9aa0a8',
        'border-width': 0.85,
        'border-color': '#20242a',
        'border-opacity': 0.5,
        label: 'data(displayLabel)',
        'font-family': FONT,
        'font-size': 13.5,
        'font-weight': 450,
        color: '#1c2128',
        'text-valign': 'center',
        'text-halign': 'right',
        'text-margin-x': 7,
        'text-wrap': 'ellipsis',
        'text-max-width': 240,
        'min-zoomed-font-size': 7,
        'overlay-opacity': 0,
        'transition-property': 'opacity, text-opacity, border-width',
        'transition-duration': 140,
      },
    },
    {
      selector: 'node.question',
      style: {
        'background-color': NODE_KINDS.question.color,
        'border-opacity': 1,
        'font-size': 15,
        'font-weight': 600,
        'text-wrap': 'wrap',
        'text-max-width': 230,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-margin-x': 0,
        'text-margin-y': 8,
        'text-background-color': '#fafaf9',
        'text-background-opacity': 0.85,
        'text-background-padding': 3,
      },
    },
    { selector: 'node.paper', style: { 'background-color': NODE_KINDS.paper.color, 'font-weight': 550 } },
    {
      selector: 'node.paper_abstract',
      style: { 'background-color': NODE_KINDS.paper_abstract.color, 'font-weight': 550, 'border-style': 'dashed', 'border-width': 1.2 },
    },
    {
      selector: 'node.paper_new',
      style: {
        'background-color': NODE_KINDS.paper_new.color, 'font-weight': 600,
        'border-width': 2.6, 'border-color': NODE_KINDS.paper_new.ring, 'border-opacity': 1,
      },
    },
    {
      selector: 'node.claim-node',
      style: { 'font-size': 12, color: '#3b3f47', 'text-max-width': 120 },
    },
    // Labels sit on the outward side of the ring so they never run into the centre.
    { selector: 'node[side = "left"]', style: { 'text-halign': 'left', 'text-margin-x': -7 } },
    // Paper labels ("Node N") are centred above their node so they stay clear of the claim fan.
    {
      selector: 'node.paper-node',
      style: {
        'text-valign': 'top',
        'text-halign': 'center',
        'text-margin-x': 0,
        'text-margin-y': -6,
        'text-max-width': 140,
        'text-background-color': '#fafaf9',
        'text-background-opacity': 0.85,
        'text-background-padding': 2,
      },
    },
    { selector: 'node.claim', style: { 'background-color': NODE_KINDS.claim.color } },
    { selector: 'node.claim_new', style: { 'background-color': NODE_KINDS.claim_new.color } },
    {
      selector: 'node.discovery',
      style: {
        'background-color': '#f3f4f6',
        'border-style': 'dashed',
        'border-opacity': 0.45,
        'font-size': 10.5,
        color: '#5b5d66',
      },
    },
    { selector: 'node.hover', style: { 'border-width': 1.6, 'border-opacity': 1 } },
    { selector: 'node:selected', style: { 'border-width': 2.2, 'border-color': '#20242a', 'border-opacity': 1 } },
    { selector: 'node.match', style: { 'font-weight': 650 } },

    // ---- edges ----
    {
      selector: 'edge',
      style: {
        width: 0.8,
        'curve-style': 'straight',
        'line-color': INK,
        'line-opacity': 0.2,
        'overlay-opacity': 0,
        'z-index': 1,
        'transition-property': 'opacity, width',
        'transition-duration': 140,
      },
    },
    // Structural edges: faint, never coloured, not clickable (they are not evidence).
    { selector: 'edge.structural', style: { events: 'no' } },
    { selector: 'edge.question_paper', style: { 'line-opacity': 0.3, 'line-style': 'dashed', 'line-dash-pattern': [1.5, 3.5], width: 0.9 } },
    { selector: 'edge.has_claim', style: { 'line-opacity': 0.22, width: 0.8 } },
    { selector: 'edge.discovered', style: { 'line-opacity': 0.2, 'line-style': 'dashed', 'line-dash-pattern': [1.5, 3.5], width: 0.7 } },
    // Evidence edges: coloured, thicker, drawn above structure.
    { selector: 'edge.evidence', style: { 'z-index': 5, 'line-opacity': 0.85 } },
    // Transparent wide twins of evidence edges: larger click/hover target, never drawn.
    { selector: 'edge.evidence-hit', style: { width: 14, 'line-opacity': 0, 'z-index': 4, 'transition-duration': 0 } },
  ]
  for (const [key, r] of Object.entries(RELATIONSHIPS)) {
    style.push({
      selector: `edge.rel-${key}`,
      style: {
        'line-color': r.color,
        'line-opacity': r.opacity ?? 0.85,
        width: r.width,
        'line-style': r.line === 'solid' ? 'solid' : 'dashed',
        ...(DASH[r.line] ? { 'line-dash-pattern': DASH[r.line] } : {}),
      },
    })
  }
  style.push(
    { selector: 'edge.evidence.insufficient', style: { opacity: 0.5 } },
    { selector: 'edge.evidence.hover', style: { width: 2.6, 'line-opacity': 1 } },
    { selector: 'edge.evidence:selected', style: { width: 3, 'line-opacity': 1, 'z-index': 9 } },
  )
  for (const [key, imp] of Object.entries(IMPACTS)) {
    style.push({ selector: `node.impact-${key}`, style: { 'border-width': 3, 'border-color': imp.color, 'border-opacity': 1 } })
  }
  style.push(
    { selector: '.faded', style: { opacity: 0.18, 'text-opacity': 0.18 } },
    { selector: '.hidden', style: { display: 'none' } },
  )
  return style
}
