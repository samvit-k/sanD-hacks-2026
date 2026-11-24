// How to describe a paper's latest claim-extraction outcome (backend: papers.extraction_report).
// The wording never claims more than the run shows: "no relevant findings IDENTIFIED for this question", not
// "the paper contains no relevant findings". Gemini's note is a model assessment, never evidence.

export const OUTCOME = {
  claims: 'claims_extracted',
  none: 'no_relevant_findings',
  rejected: 'all_candidates_rejected',
}

/**
 * null when there is nothing special to say (claims were extracted, or the outcome was not recorded);
 * otherwise { kind: 'none' | 'rejected', badge, title, detail, question, note }.
 */
export function describeExtraction(report) {
  if (!report?.outcome || report.outcome === OUTCOME.claims) return null
  const question = report.question ?? null
  const note = report.model_note ?? null
  if (report.outcome === OUTCOME.none) {
    return {
      kind: 'none',
      badge: 'No relevant findings',
      title: 'No relevant findings identified for this question',
      detail: 'Gemini read the paper and returned no candidate findings that bear on the project\'s central question. '
        + 'The paper is stored. If the question does not match your papers, change it (papers are kept) or add papers '
        + 'on this topic.',
      question,
      note,
    }
  }
  if (report.outcome === OUTCOME.rejected) {
    const n = report.returned ?? 0
    return {
      kind: 'rejected',
      badge: 'Findings failed verification',
      title: `${n} candidate finding${n === 1 ? '' : 's'} failed citation verification`,
      detail: 'Gemini proposed findings, but none cited valid sentences from this paper, so none were added.',
      question,
      note,
    }
  }
  return null
}