"""Generate FICTIONAL sample fixtures (frontend/public/fixtures/) through the real graph builders.

Used only so the frontend can be built before real papers are seeded. Replace them with real
snapshots (scripts/snapshot.py) before the demo. Everything here is labeled "[Sample]".
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compare import condition_diff  # noqa: E402
from app.config import ROOT  # noqa: E402
from app.graph import build_graph, build_impact_summary  # noqa: E402

OUT = ROOT / "frontend" / "public" / "fixtures"
Q = "Do AI tutors improve learning?"

PAPERS = [
    {"id": "sample-a", "title": "[Sample] AI tutoring in grade 8 mathematics: a 12-week RCT", "year": 2023,
     "access_level": "full", "relevance": 0.91, "doi": None},
    {"id": "sample-b", "title": "[Sample] A two-week classroom trial of an AI tutor", "year": 2022,
     "access_level": "full", "relevance": 0.84},
    {"id": "sample-c", "title": "[Sample] Who benefits? AI tutors and prior achievement", "year": 2024,
     "access_level": "abstract", "relevance": 0.72},
]
PAPER_D = {"id": "sample-d", "title": "[Sample] Do AI tutor gains last? A six-month follow-up", "year": 2025,
           "access_level": "full", "relevance": 0.88, "is_new": True}

S = {}

def sent(pid, page, idx, section, text):
    sid = f"{pid}:p{page}_s{idx}"
    S[sid] = {"id": sid, "paper_id": pid, "page": page, "idx": idx, "section": section, "text": text}
    return sid

a1 = sent("sample-a", 6, 3, "results", "After 12 weeks, students using the AI tutor scored 25% higher on the "
          "end-of-unit mathematics test than the control group (p < 0.01).")
a2 = sent("sample-a", 6, 4, "results", "The effect was consistent across the four participating schools.")
a3 = sent("sample-a", 3, 2, "methods", "We randomly assigned 240 grade 8 students to AI tutoring or business-as-usual instruction.")
b1 = sent("sample-b", 4, 1, "results", "After two weeks, test scores did not differ significantly between the "
          "tutor and control groups (p = 0.41).")
b2 = sent("sample-b", 5, 2, "discussion", "The short duration may have been insufficient for effects to emerge.")
c1 = sent("sample-c", 1, 4, "abstract", "Gains from AI tutoring were roughly twice as large for students with "
          "below-median prior achievement.")
d1 = sent("sample-d", 7, 2, "results", "Test-score gains observed at the end of a 12-week AI tutoring programme "
          "were no longer detectable six months later.")
d2 = sent("sample-d", 7, 3, "results", "Retention did not differ by students' prior achievement.")

CA = {"population": "Grade 8 students", "sample_size": "n=240", "method": "Randomized controlled trial",
      "duration": "12 weeks", "outcome_measure": "End-of-unit mathematics test", "significance": "p < 0.01"}
CB = {"population": "Grade 8 students", "sample_size": "n=60", "method": "Randomized controlled trial",
      "duration": "2 weeks", "outcome_measure": "End-of-unit mathematics test", "significance": "p = 0.41",
      "limitations": "Short duration"}
CC = {"population": "Grade 7-9 students", "method": "Quasi-experimental", "outcome_measure": "Mathematics test"}
CD = {"population": "Grade 8 students", "sample_size": "n=180", "method": "Longitudinal follow-up of an RCT",
      "duration": "12 weeks + 6-month follow-up", "outcome_measure": "Retention test score"}

CLAIMS = [
    {"id": 1, "paper_id": "sample-a", "text": "AI tutoring raised grade 8 math test scores by 25% after 12 weeks.",
     "conditions": CA, "sentence_ids": [a1, a2]},
    {"id": 2, "paper_id": "sample-b", "text": "Two weeks of AI tutoring produced no significant test-score improvement.",
     "conditions": CB, "sentence_ids": [b1]},
    {"id": 3, "paper_id": "sample-c", "text": "AI tutoring gains were larger for students with lower prior achievement.",
     "conditions": CC, "sentence_ids": [c1]},
]
CLAIM_D = [
    {"id": 4, "paper_id": "sample-d", "text": "12-week AI tutoring gains were not retained six months later.",
     "conditions": CD, "sentence_ids": [d1]},
    {"id": 5, "paper_id": "sample-d", "text": "Retention of gains did not differ by prior achievement.",
     "conditions": CD, "sentence_ids": [d2]},
]

def rel(rid, a, b, label, expl, keys, cited, sim, insufficient=False):
    ca = next(c for c in CLAIMS + CLAIM_D if c["id"] == a)
    cb = next(c for c in CLAIMS + CLAIM_D if c["id"] == b)
    return {"id": rid, "claim_a": a, "claim_b": b, "label": label, "explanation": expl, "key_differences": keys,
            "cited_sentence_ids": cited, "similarity": sim, "insufficient_evidence": insufficient,
            "condition_diff": condition_diff(ca["conditions"], cb["conditions"])}

RELS = [
    rel(1, 1, 2, "apparent_contradiction",
        "Both studies measured the same test in the same population, but A ran for 12 weeks and B for 2 weeks; "
        "B's authors note the short duration may explain the null result.", ["duration", "sample_size"],
        [a1, b1, b2], 0.83),
    rel(2, 1, 3, "supports",
        "C reports positive gains (larger for low achievers), compatible with A's positive overall effect, "
        "although C is abstract-only and quasi-experimental.", ["population", "method"], [a1, c1], 0.71),
]
RELS_D = [
    rel(3, 1, 4, "apparent_contradiction",
        "A measured scores immediately after 12 weeks; D measured whether the same kind of gains persisted six "
        "months later. Both results can hold.", ["outcome_measure", "duration"], [a1, d1], 0.86),
    rel(4, 3, 5, "not_comparable",
        "C concerns the size of immediate gains by prior achievement; D concerns retention by prior "
        "achievement, a different outcome.", ["outcome_measure"], [c1, d2], 0.66),
    rel(5, 2, 4, "unresolved", "Insufficient evidence: the assessment could not be verified against valid source "
        "sentences from both papers, so no judgment is shown.", [], [], 0.62, insufficient=True),
]
IMPACTS = [
    {"id": 1, "new_claim_id": 4, "prior_claim_id": 1, "relationship_id": 3, "impact": "qualifies",
     "rationale": "D does not dispute A's immediate 25% gain; it shows that, under comparable conditions, the gain "
     "was not detectable six months later. A's finding holds for short-term outcomes only.",
     "cited_sentence_ids": [a1, d1], "insufficient_evidence": False},
    {"id": 2, "new_claim_id": 5, "prior_claim_id": 3, "relationship_id": 4, "impact": "leaves_unresolved",
     "rationale": "D measured retention rather than immediate gains, so it neither supports nor conflicts with C's "
     "finding about immediate gains by prior achievement.", "cited_sentence_ids": [c1, d2],
     "insufficient_evidence": False},
    {"id": 3, "new_claim_id": 4, "prior_claim_id": 2, "relationship_id": 5, "impact": "leaves_unresolved",
     "rationale": "Insufficient evidence: the assessment could not be verified against valid source sentences from "
     "both papers, so no judgment is shown.", "cited_sentence_ids": [], "insufficient_evidence": True},
]
DISCOVERY = {"query": PAPER_D["title"], "source": "openalex", "fallback_reason": "Bright Data not configured",
             "for_paper_id": "sample-d", "cached": True, "results": [
                 {"title": "[Sample] Long-term effects of intelligent tutoring systems: a review", "year": 2021,
                  "doi": None, "abstract": "A fictional review used only as sample data.", "access_level": "abstract",
                  "resolved": True, "oa_url": None},
                 {"title": "[Sample] Fade-out of educational intervention effects", "year": 2019, "doi": None,
                  "abstract": None, "access_level": "metadata", "resolved": False}]}

def write(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT / name}")

if __name__ == "__main__":
    g = build_graph(Q, PAPERS, CLAIMS, RELS, S, source="sample")
    write("graph.json", g)
    papers_all = PAPERS + [PAPER_D]
    g2 = build_graph(Q, papers_all, CLAIMS + CLAIM_D, RELS + RELS_D, S, discovery=DISCOVERY, source="sample")
    write("graph_with_new.json", g2)
    write("paper_d_result.json", build_impact_summary(PAPER_D, CLAIMS + CLAIM_D, IMPACTS, S, papers_all,
                                                      extraction={"returned": 2, "kept": 2,
                                                                  "rejected_invalid_citations": 0,
                                                                  "rejected_empty_text": 0, "rejected_over_limit": 0},
                                                      status="precomputed"))