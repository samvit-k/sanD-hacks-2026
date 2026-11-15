"""Gemini claim extraction + strict sentence-ID validation."""
from typing import Dict, List, Tuple

from .citations import validate_single_paper_citations
from .contracts import ExtractionResult

# When a paper exceeds the prompt limit, these sections are included first.
PRIORITY_SECTIONS = ("results", "conclusion", "discussion", "abstract", "front")

EXTRACTION_PROMPT = """You are extracting testable scientific findings for an evidence graph.

Research question: {question}
Paper: {title}

Below are the paper's sentences. Each line starts with the sentence's full ID in brackets, then its section.
{truncation_note}
{sentences}

Task: identify the {max_claims} most important TESTABLE findings from this paper that bear on the
research question (results the study actually reports, not background, prior work, or hypotheses).

Rules:
- Cite evidence ONLY by copying full sentence IDs exactly as shown (e.g. "{example_id}").
  Every ID you cite must appear above. Do not write quotations; the system rebuilds quotes from the IDs.
- Cite the 1-3 sentences that most directly state each finding (prefer Results sentences).
- "text" restates the finding faithfully and concisely. Do not strengthen or generalize it.
- Fill study conditions from the paper. Use null for anything the paper does not report.
  Never guess sample sizes, effect sizes, or significance.
- If fewer than {max_claims} findings are clearly reported, return fewer.
"""


def select_prompt_sentences(sentences: List[Dict], limit: int) -> List[Dict]:
    """All sentences if within `limit`; otherwise priority sections first, then the rest
    (e.g. Methods, for study conditions) in document order, until the limit. Output keeps document order."""
    if len(sentences) <= limit:
        return list(sentences)
    order = {s["id"]: i for i, s in enumerate(sentences)}
    priority = [s for s in sentences if s.get("section") in PRIORITY_SECTIONS]
    rest = [s for s in sentences if s.get("section") not in PRIORITY_SECTIONS]
    chosen = (priority + rest)[:limit]
    return sorted(chosen, key=lambda s: order[s["id"]])


def build_extraction_prompt(question: str, title: str, sentences: List[Dict], max_claims: int, limit: int) -> str:
    chosen = select_prompt_sentences(sentences, limit)
    note = ""
    if len(chosen) < len(sentences):
        note = (f"(Long paper: showing {len(chosen)} of {len(sentences)} sentences, "
                "prioritizing Results, Conclusion and Discussion.)\n")
    return EXTRACTION_PROMPT.format(
        question=question,
        title=title,
        truncation_note=note,
        sentences="\n".join(f"[{s['id']}] ({s.get('section', 'front')}) {s['text']}" for s in chosen),
        max_claims=max_claims,
        example_id=chosen[0]["id"] if chosen else "paper:p1_s1",
    )


def validate_claims(
    raw: ExtractionResult, paper_id: str, stored_paper: Dict[str, str], max_claims: int
) -> Tuple[List[Dict], Dict]:
    """Strict: a claim is rejected if it cites no IDs, or if ANY cited ID is blank, unknown,
    or from another paper. The report contains counts only — never the rejected IDs."""
    kept: List[Dict] = []
    report = {"returned": len(raw.claims), "kept": 0,
              "rejected_invalid_citations": 0, "rejected_empty_text": 0, "rejected_over_limit": 0}
    for claim in raw.claims:
        text = (claim.text or "").strip()
        ids, ok = validate_single_paper_citations(claim.sentence_ids, paper_id, stored_paper)
        if not text:
            report["rejected_empty_text"] += 1
            continue
        if not ok:
            report["rejected_invalid_citations"] += 1
            continue
        if len(kept) >= max_claims:
            report["rejected_over_limit"] += 1
            continue
        kept.append({
            "paper_id": paper_id,
            "text": text,
            "sentence_ids": ids,
            "conditions": claim.conditions.model_dump(),
        })
    report["kept"] = len(kept)
    return kept, report
