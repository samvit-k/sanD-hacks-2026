"""Claim comparison and new-paper impact: prompts, deterministic condition diff, guardrails.

The condition diff only DESCRIBES differences; Gemini makes every judgment.
Impact is a separate Gemini judgment — it is never mapped from relationship labels.
If citation validation fails, the model's unsupported judgment is discarded entirely.
"""
import json
import re
from typing import Dict, List, Optional

from .citations import validate_two_paper_citations
from .contracts import CONDITION_FIELDS, NOT_REPORTED, ImpactJudgment, PairJudgment

INSUFFICIENT = ("Insufficient evidence: the assessment could not be verified against valid source "
                "sentences from both papers, so no judgment is shown.")


def _norm(v: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(v)).strip().lower().rstrip(".")


def condition_diff(a: Dict, b: Dict) -> Dict:
    """Per field: "same", "not reported" (both missing), or [value_a, value_b]."""
    diff: Dict = {}
    for field in CONDITION_FIELDS:
        va, vb = (a or {}).get(field), (b or {}).get(field)
        va = va if va not in (None, "") else None
        vb = vb if vb not in (None, "") else None
        if va is None and vb is None:
            diff[field] = NOT_REPORTED
        elif va is not None and vb is not None and _norm(va) == _norm(vb):
            diff[field] = "same"
        else:
            diff[field] = [va or NOT_REPORTED, vb or NOT_REPORTED]
    return diff


def _claim_block(tag: str, claim: Dict) -> str:
    conds = {k: v for k, v in (claim.get("conditions") or {}).items() if v}
    lines = "\n".join(f"  [{s['id']}] {s['text']}" for s in claim["context"])
    abstract_note = " (ABSTRACT ONLY — full text not available)" if claim.get("access_level") == "abstract" else ""
    return (
        f"{tag} — paper: {claim['paper_title']}{abstract_note}\n"
        f"Claim: {claim['text']}\n"
        f"Study conditions: {json.dumps(conds, ensure_ascii=False) if conds else 'none reported'}\n"
        f"Source sentences (cite by ID):\n{lines}\n"
    )


PAIR_PROMPT = """You are verifying the relationship between two scientific claims about: {question}

{block_a}
{block_b}
Condition comparison computed by code (descriptive only — it does NOT decide the label):
{diff}

{dependence}
First decide comparability:
- same_hypothesis_same_measure: both findings test the same hypothesis with the same or an equivalent outcome measure.
- same_hypothesis_different_setting: same hypothesis, but a different dataset, population, setting or metric.
- thematic_only: related topic, but the findings test DIFFERENT hypotheses (different interventions, mechanisms or
  components), even if both report improvements.

Then choose ONE label:
- supports: BOTH findings test the same hypothesis and point the same way, so one is direct empirical evidence for
  the other. Requires comparability other than thematic_only.
- genuine_contradiction: the studies are comparable on the conditions that matter AND their results are incompatible.
- apparent_contradiction: the results seem to conflict, but differences in study conditions plausibly explain it.
- not_comparable: related topic, but they measure different things or populations, so no direct comparison is meaningful.
- unresolved: the cited evidence is insufficient to judge.

Rules:
- Judge from the source sentences, not just the claim summaries.
- Thematic similarity is NOT support. "Both report that some advanced method improves performance" is not support
  unless they test the same method/hypothesis. The absence of a contradiction is not support: use not_comparable.
- Different datasets, settings or metrics can still support when the hypothesis is the same; say what differs and
  how far the comparison holds in the explanation.
- If the papers share authors, the label is still decided on the evidence, but say in the explanation that the
  support comes from related work by overlapping authors. Never describe support as independent.
- Different conditions do NOT automatically make a contradiction "apparent"; decide whether the
  differences actually matter for these results. Similar results under different conditions can still support.
- A statistically non-significant result does not by itself refute a significant one.
- Be cautious with abstract-only evidence.
- cited_sentence_ids: copy IDs exactly from the source sentences above; include at least one from
  Claim A AND one from Claim B. Cite no other IDs.
- key_differences: list only the condition differences that matter for your judgment (may be empty).
"""

IMPACT_PROMPT = """A researcher added a NEW paper to an existing evidence library on: {question}
Decide how the NEW finding affects the EARLIER finding.

{block_prior}
{block_new}
Condition comparison computed by code (descriptive only):
{diff}

{dependence}
Earlier relationship assessment (context only — do NOT simply convert this label into an impact):
label={rel_label}; explanation={rel_explanation}

Choose ONE impact on the EARLIER finding:
- strengthens: the new evidence adds support for the earlier finding: it tests the same hypothesis under comparable
  conditions and points the same way. Whether that support is independent is a separate question (see the papers'
  relationship above); never call it independent.
- challenges: the new evidence, from a comparable study, conflicts with the earlier finding.
- qualifies: the new evidence limits or adds conditions to the earlier finding (e.g. holds only short-term,
  only for some populations) without refuting it.
- leaves_unresolved: the new evidence does not settle the earlier finding either way.

Rules:
- Weigh the original source sentences and study conditions yourself.
- Never say a finding is "true", "false", or "disproved". Describe how the evidence changes.
- A non-significant result does not by itself refute a significant one.
- Thematic similarity is not support: if the findings test different hypotheses, use leaves_unresolved.
- If the papers share authors, say so in the rationale; the new paper is related work, not independent corroboration.
- cited_sentence_ids: copy IDs exactly from the source sentences above; include at least one from the
  EARLIER and one from the NEW paper. Cite no other IDs.
"""


def _dependence_block(dependence: Optional[str]) -> str:
    return f"Relationship between the two papers (computed from their author lists): {dependence}\n" if dependence else ""


def build_pair_prompt(question: str, a: Dict, b: Dict, diff: Dict, dependence: Optional[str] = None) -> str:
    return PAIR_PROMPT.format(question=question, block_a=_claim_block("CLAIM A", a),
                              block_b=_claim_block("CLAIM B", b), diff=json.dumps(diff, ensure_ascii=False, indent=1),
                              dependence=_dependence_block(dependence))


def build_impact_prompt(question: str, prior: Dict, new: Dict, diff: Dict, rel: Dict,
                        dependence: Optional[str] = None) -> str:
    return IMPACT_PROMPT.format(
        dependence=_dependence_block(dependence),
        question=question,
        block_prior=_claim_block("EARLIER FINDING", prior),
        block_new=_claim_block("NEW FINDING", new),
        diff=json.dumps(diff, ensure_ascii=False, indent=1),
        rel_label=rel.get("label", "none"),
        rel_explanation=rel.get("explanation", ""),
    )


THEMATIC_NOTE = ("Label set by SpiderMonkey: Gemini chose “supports” but judged the findings related only "
                 "thematically (they test different hypotheses). Thematic similarity is not support, so the "
                 "relationship is shown as not comparable. Gemini's explanation is kept unchanged.")


def apply_pair_guardrail(j: PairJudgment, paper_a: str, paper_b: str, stored_paper: Dict[str, str]) -> Dict:
    ids, ok = validate_two_paper_citations(j.cited_sentence_ids, paper_a, paper_b, stored_paper)
    if ok:
        label, note = j.label, None
        # Consistency guard on Gemini's OWN structured answer (never on the condition diff): "supports" needs
        # the same hypothesis. Recorded transparently (model_label + label_note), never silent.
        if j.label == "supports" and j.comparability == "thematic_only":
            label, note = "not_comparable", THEMATIC_NOTE
        return {"label": label, "explanation": j.explanation.strip(), "key_differences": list(j.key_differences),
                "cited_sentence_ids": ids, "insufficient_evidence": False,
                "comparability": j.comparability, "model_label": j.label, "label_note": note}
    # Unsupported judgment: discard the model's label, explanation, differences and citations.
    return {"label": "unresolved", "explanation": INSUFFICIENT, "key_differences": [],
            "cited_sentence_ids": [], "insufficient_evidence": True,
            "comparability": None, "model_label": None, "label_note": None}


def apply_impact_guardrail(j: ImpactJudgment, prior_paper: str, new_paper: str, stored_paper: Dict[str, str]) -> Dict:
    ids, ok = validate_two_paper_citations(j.cited_sentence_ids, prior_paper, new_paper, stored_paper)
    if ok:
        return {"impact": j.impact, "rationale": j.rationale.strip(), "cited_sentence_ids": ids,
                "insufficient_evidence": False}
    return {"impact": "leaves_unresolved", "rationale": INSUFFICIENT, "cited_sentence_ids": [],
            "insufficient_evidence": True}


def dedupe_pairs(rows: List[Dict], threshold: float) -> List[Dict]:
    """rows: {a, b, sim}. Keep sim >= threshold, one entry per unordered pair (a < b)."""
    best: Dict = {}
    for r in rows:
        if r["sim"] is None or r["sim"] < threshold or r["a"] == r["b"]:
            continue
        key = (min(r["a"], r["b"]), max(r["a"], r["b"]))
        if key not in best or r["sim"] > best[key]:
            best[key] = r["sim"]
    return [{"a": k[0], "b": k[1], "sim": s} for k, s in sorted(best.items())]
