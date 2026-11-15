// Single source of display vocabulary and colours. Wording describes how evidence relates;
// it never declares a finding true or false.

export const NODE_KINDS = {
  question: { label: 'Research question', color: '#20242a' },
  paper: { label: 'Paper', color: '#6366A8' },
  paper_new: { label: 'Evaluated paper', color: '#6366A8', ring: '#0ea5e9' },
  paper_abstract: { label: 'Abstract-only paper', color: '#8A86C4', dashed: true },
  claim: { label: 'Claim', color: '#2E8C84' },
  claim_new: { label: 'Claim of evaluated paper', color: '#38bdf8' },
  discovery: { label: 'Related lead (not analyzed)', color: '#e5e7eb', dashed: true },
}

export const RELATIONSHIPS = {
  supports: {
    label: 'Supports', color: '#3f9e6e', line: 'solid', width: 1.5,
    help: 'Compatible findings pointing the same way.',
  },
  genuine_contradiction: {
    label: 'Genuine contradiction', color: '#c4564b', line: 'solid', width: 2,
    help: 'Comparable studies with incompatible results.',
  },
  apparent_contradiction: {
    label: 'Apparent contradiction', color: '#c4564b', line: 'dashed', width: 1.5,
    help: 'Results seem to conflict, but study conditions plausibly explain it.',
  },
  unresolved: {
    label: 'Unresolved', color: '#b07d3a', line: 'dotted', width: 1.6,
    help: 'The cited evidence is insufficient to judge.',
  },
  not_comparable: {
    // Cytoscape ignores rgba alpha, so opacity is a separate field.
    label: 'Not comparable', color: '#1d232a', opacity: 0.32, line: 'solid', width: 1,
    help: 'Related, but measuring different things or populations.',
  },
}
export const RELATIONSHIP_ORDER = [
  'genuine_contradiction', 'apparent_contradiction', 'supports', 'unresolved', 'not_comparable',
]
export const INSUFFICIENT = {
  label: 'Insufficient evidence',
  help: 'The model’s citations could not be verified, so no judgment is shown.',
}

export const STRUCTURAL = {
  question_paper: { label: 'Question → paper', help: 'Navigation only; distance reflects relevance, not truth.' },
  has_claim: { label: 'Paper → claim', help: 'Claim extracted from this paper.' },
  discovered: { label: 'Related lead', help: 'Found by search; not analyzed.' },
}

export const IMPACTS = {
  challenges: { label: 'Challenges', color: '#c4564b' },
  qualifies: { label: 'Qualifies', color: '#b07d3a' },
  strengthens: { label: 'Strengthens', color: '#3f9e6e' },
  leaves_unresolved: { label: 'Leaves unresolved', color: '#8e8ea0' },
}
// Shared or unknown authorship behind a supportive label: shown, never used to change the label.
export const INDEPENDENCE = {
  color: '#d4a017',
  label: 'Independence not established',
  help: 'Supporting evidence from papers that share authors (or whose authors could not be read): not independent corroboration. Review needed.',
}
export const REVIEW = {
  color: '#6d4fc2',
  label: 'Reclassification proposed',
  help: 'Someone proposed a different label; the original label is shown until a person accepts the proposal.',
}
export const IMPACT_ORDER = ['challenges', 'qualifies', 'strengthens', 'leaves_unresolved']

export const CONDITION_LABELS = {
  population: 'Population',
  sample_size: 'Sample size',
  method: 'Method',
  duration: 'Duration',
  outcome_measure: 'Outcome measure',
  effect_size: 'Effect size',
  significance: 'Significance',
  limitations: 'Stated limitations',
}
export const DIFF_FIELDS = ['population', 'sample_size', 'method', 'duration', 'outcome_measure']

export const SECTION_LABELS = {
  results: 'Results', conclusion: 'Conclusion', discussion: 'Discussion', methods: 'Methods',
  abstract: 'Abstract', introduction: 'Introduction', limitations: 'Limitations', front: 'Front matter',
  other: 'Other',
}

// Words the UI must never use about findings.
export const BANNED_WORDS = ['true', 'false', 'disproves', 'disproved', 'debunked', 'proven', 'proves']
