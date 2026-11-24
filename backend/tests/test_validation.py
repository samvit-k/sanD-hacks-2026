"""Strict citation validation, extraction, and comparison guardrails."""
from app import compare, extract
from app.citations import validate_single_paper_citations, validate_two_paper_citations
from app.contracts import Conditions, ExtractedClaim, ExtractionResult, ImpactJudgment, PairJudgment

# Stored sentence -> paper map, as loaded from the `sentences` table.
STORED = {
    "pa:p1_s1": "pa", "pa:p1_s2": "pa",
    "pb:p2_s1": "pb", "pb:p2_s2": "pb",
    "pc:p1_s1": "pc",
    # An ID whose text *looks* like paper pa but is stored under pc: prefix must never be trusted.
    "pa:p9_s9": "pc",
}

# ---------- citations.py ----------
def test_two_paper_valid():
    ids, ok = validate_two_paper_citations(["pa:p1_s1", "[pb:p2_s1]", "pa:p1_s1"], "pa", "pb", STORED)
    assert ok and ids == ["pa:p1_s1", "pb:p2_s1"]

def test_two_paper_one_fabricated_id_fails_whole_judgment():
    _, ok = validate_two_paper_citations(["pa:p1_s1", "pb:p2_s1", "pb:p7_s99"], "pa", "pb", STORED)
    assert not ok

def test_two_paper_wrong_paper_id_fails():
    _, ok = validate_two_paper_citations(["pa:p1_s1", "pb:p2_s1", "pc:p1_s1"], "pa", "pb", STORED)
    assert not ok

def test_two_paper_prefix_is_not_trusted():
    _, ok = validate_two_paper_citations(["pa:p9_s9", "pb:p2_s1"], "pa", "pb", STORED)
    assert not ok

def test_two_paper_one_sided_or_empty_fails():
    assert not validate_two_paper_citations(["pa:p1_s1", "pa:p1_s2"], "pa", "pb", STORED)[1]
    assert not validate_two_paper_citations([], "pa", "pb", STORED)[1]

def test_failed_validation_returns_only_verified_ids():
    ids, ok = validate_two_paper_citations(["pa:p1_s1", "pb:p7_s99", "pc:p1_s1", "pa:p9_s9", "pb:p2_s1"],
                                           "pa", "pb", STORED)
    assert not ok
    assert ids == ["pa:p1_s1", "pb:p2_s1"]  # fabricated, wrong-paper and spoofed IDs never returned
    ids, ok = validate_single_paper_citations(["pa:p1_s2", "pb:p2_s1", "zz:p1_s1"], "pa", STORED)
    assert not ok and ids == ["pa:p1_s2"]

def test_blank_ids_are_not_silently_ignored():
    for blank in ["", "   ", "[]", None]:
        _, ok = validate_two_paper_citations(["pa:p1_s1", blank, "pb:p2_s1"], "pa", "pb", STORED)
        assert not ok, f"blank id {blank!r} was ignored"
        ids, ok = validate_single_paper_citations(["pa:p1_s1", blank], "pa", STORED)
        assert not ok and ids == ["pa:p1_s1"]

def test_single_paper_strict():
    assert validate_single_paper_citations(["pa:p1_s1"], "pa", STORED)[1]
    assert not validate_single_paper_citations(["pa:p1_s1", "pa:p5_s5"], "pa", STORED)[1]  # fabricated
    assert not validate_single_paper_citations(["pa:p1_s1", "pb:p2_s1"], "pa", STORED)[1]  # wrong paper
    assert not validate_single_paper_citations(["pa:p9_s9"], "pa", STORED)[1]  # prefix spoof
    assert not validate_single_paper_citations([], "pa", STORED)[1]

# ---------- extract.py ----------
def _claim(text, ids):
    return ExtractedClaim(text=text, sentence_ids=ids, conditions=Conditions(duration="12 weeks"))

def test_extraction_rejects_claims_with_any_bad_citation():
    raw = ExtractionResult(claims=[
        _claim("Good claim.", ["pa:p1_s1", "pa:p1_s2"]),
        _claim("One fabricated ID.", ["pa:p1_s1", "pa:p3_s7"]),
        _claim("One wrong-paper ID.", ["pa:p1_s2", "pb:p2_s1"]),
        _claim("Prefix spoof.", ["pa:p9_s9"]),
        _claim("No IDs.", []),
        _claim("Blank ID.", ["pa:p1_s1", ""]),
        _claim("   ", ["pa:p1_s1"]),
    ])
    kept, report = extract.validate_claims(raw, "pa", STORED, max_claims=3)
    assert [c["text"] for c in kept] == ["Good claim."]
    assert kept[0]["sentence_ids"] == ["pa:p1_s1", "pa:p1_s2"]
    assert kept[0]["conditions"]["duration"] == "12 weeks"
    assert report == {"returned": 7, "kept": 1, "rejected_invalid_citations": 5,
                      "rejected_empty_text": 1, "rejected_over_limit": 0,
                      "outcome": "claims_extracted", "model_note": None}
    assert "pa:p3_s7" not in str(report) and "pb:p2_s1" not in str(report)  # no rejected IDs leak

def test_extraction_respects_max_claims():
    raw = ExtractionResult(claims=[_claim(f"Claim {i}.", ["pa:p1_s1"]) for i in range(5)])
    kept, _ = extract.validate_claims(raw, "pa", STORED, max_claims=2)
    assert len(kept) == 2

def _sents(spec):
    out, n = [], 0
    for section, count in spec:
        for _ in range(count):
            n += 1
            out.append({"id": f"p:p1_s{n}", "section": section, "text": f"{section} sentence {n}."})
    return out

def test_prompt_selection_keeps_all_when_short():
    s = _sents([("introduction", 3), ("results", 2)])
    assert extract.select_prompt_sentences(s, 10) == s

def test_prompt_selection_prioritizes_results_and_conclusion():
    s = _sents([("introduction", 50), ("methods", 30), ("results", 20), ("conclusion", 5)])
    chosen = extract.select_prompt_sentences(s, 40)
    sections = [c["section"] for c in chosen]
    assert len(chosen) == 40
    assert sections.count("results") == 20 and sections.count("conclusion") == 5
    assert sections.count("introduction") == 15  # remaining budget filled in document order
    assert [c["id"] for c in chosen] == sorted((c["id"] for c in chosen), key=lambda i: int(i.split("_s")[1]))

def test_prompt_notes_truncation():
    s = _sents([("introduction", 30), ("results", 5)])
    prompt = extract.build_extraction_prompt("Q?", "T", s, 3, limit=10)
    assert "showing 10 of 35 sentences" in prompt and "(results) results sentence 31." in prompt

# ---------- compare.py ----------
def test_condition_diff_describes_only():
    a = {"population": "Grade 8 students", "duration": "12 weeks", "outcome_measure": "Test score", "method": None}
    b = {"population": "grade 8 students.", "duration": "2 weeks", "outcome_measure": "test score"}
    d = compare.condition_diff(a, b)
    assert d == {"population": "same", "sample_size": "not reported", "method": "not reported",
                 "duration": ["12 weeks", "2 weeks"], "outcome_measure": "same"}
    assert compare.condition_diff({"sample_size": "n=40"}, {})["sample_size"] == ["n=40", "not reported"]

def test_pair_guardrail_passes_valid():
    j = PairJudgment(label="apparent_contradiction", explanation="Durations differ.",
                     key_differences=["duration"], cited_sentence_ids=["pa:p1_s1", "pb:p2_s1"])
    r = compare.apply_pair_guardrail(j, "pa", "pb", STORED)
    assert r["label"] == "apparent_contradiction" and not r["insufficient_evidence"]

def test_pair_guardrail_hides_unsupported_judgment():
    j = PairJudgment(label="genuine_contradiction", explanation="Paper B refutes paper A.",
                     key_differences=["duration"], cited_sentence_ids=["pa:p1_s1", "pb:p2_s1", "pb:p8_s8"])
    r = compare.apply_pair_guardrail(j, "pa", "pb", STORED)
    assert r == {"label": "unresolved", "explanation": compare.INSUFFICIENT, "key_differences": [],
                 "cited_sentence_ids": [], "insufficient_evidence": True,
                 "comparability": None, "model_label": None, "label_note": None}
    assert "refutes" not in str(r) and "genuine" not in str(r)

def test_impact_guardrail_hides_unsupported_judgment():
    ok = ImpactJudgment(impact="qualifies", rationale="Short-term only.", cited_sentence_ids=["pa:p1_s1", "pb:p2_s2"])
    assert compare.apply_impact_guardrail(ok, "pa", "pb", STORED)["impact"] == "qualifies"
    bad = ImpactJudgment(impact="challenges", rationale="Disproves it.", cited_sentence_ids=["pa:p1_s1", "pc:p1_s1"])
    r = compare.apply_impact_guardrail(bad, "pa", "pb", STORED)
    assert r["impact"] == "leaves_unresolved" and r["rationale"] == compare.INSUFFICIENT
    assert r["cited_sentence_ids"] == [] and "Disproves" not in str(r)

def test_dedupe_pairs():
    rows = [{"a": 1, "b": 2, "sim": 0.8}, {"a": 2, "b": 1, "sim": 0.82}, {"a": 1, "b": 3, "sim": 0.4}]
    assert compare.dedupe_pairs(rows, 0.6) == [{"a": 1, "b": 2, "sim": 0.82}]