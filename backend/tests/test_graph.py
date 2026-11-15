from app.graph import build_graph, build_impact_summary

PAPERS = [
    {"id": "pa", "title": "Paper A", "access_level": "full", "is_new": False, "relevance": 0.9},
    {"id": "pb", "title": "Paper B", "access_level": "abstract", "is_new": False, "relevance": 0.5},
    {"id": "pd", "title": "Paper D", "access_level": "full", "is_new": True, "relevance": 0.8},
    {"id": "pe", "title": "Paper E (earlier upload)", "access_level": "full", "is_new": True, "relevance": 0.7},
]
SENT = {
    "pa:p1_s1": {"paper_id": "pa", "page": 1, "section": "results", "text": "A scored higher."},
    "pb:p1_s1": {"paper_id": "pb", "page": 1, "section": "abstract", "text": "B found no effect."},
    "pd:p2_s3": {"paper_id": "pd", "page": 2, "section": "results", "text": "D gains faded."},
    "pe:p1_s1": {"paper_id": "pe", "page": 1, "section": "results", "text": "E gains held."},
}
CLAIMS = [
    {"id": 1, "paper_id": "pa", "text": "A claim", "conditions": {"duration": "12 weeks"}, "sentence_ids": ["pa:p1_s1"]},
    {"id": 2, "paper_id": "pb", "text": "B claim", "conditions": {}, "sentence_ids": ["pb:p1_s1"]},
    {"id": 3, "paper_id": "pd", "text": "D claim", "conditions": {}, "sentence_ids": ["pd:p2_s3"]},
    {"id": 4, "paper_id": "pe", "text": "E claim", "conditions": {}, "sentence_ids": ["pe:p1_s1"]},
]
RELS = [
    {"id": 10, "claim_a": 1, "claim_b": 2, "label": "apparent_contradiction", "insufficient_evidence": False,
     "cited_sentence_ids": ["pa:p1_s1", "pb:p1_s1"], "explanation": "x", "condition_diff": {}},
    {"id": 11, "claim_a": 1, "claim_b": 3, "label": "unresolved", "insufficient_evidence": True,
     "cited_sentence_ids": [], "explanation": "Insufficient evidence", "condition_diff": {}},
]


def test_graph_nodes_edges_and_question_edges():
    g = build_graph("Q?", PAPERS, CLAIMS, RELS, SENT)
    types = {e["data"]["type"] for e in g["edges"]}
    assert types == {"question_paper", "has_claim", "relationship"}
    qp = [e["data"] for e in g["edges"] if e["data"]["type"] == "question_paper"]
    assert {(e["source"], e["target"]) for e in qp} == {("q", f"paper:{p['id']}") for p in PAPERS}
    assert qp[0]["relevance"] == 0.9
    # question edges are never confused with claim relationships
    rel_edges = [e["data"] for e in g["edges"] if e["data"]["type"] == "relationship"]
    assert all(e["source"].startswith("claim:") and e["target"].startswith("claim:") for e in rel_edges)
    assert g["papers"]["pb"]["access_level"] == "abstract"


def test_graph_relationship_details_rebuild_quotes():
    g = build_graph("Q?", PAPERS, CLAIMS, RELS, SENT)
    r = g["relationships"]["10"]
    assert r["cited"]["a"][0]["text"] == "A scored higher." and r["cited"]["b"][0]["page"] == 1
    assert g["relationships"]["11"]["cited"] == {"a": [], "b": []}
    assert g["claims"]["1"]["quotes"][0]["section"] == "results"


def test_impact_summary_only_includes_current_paper():
    impacts = [
        {"id": 1, "new_claim_id": 3, "prior_claim_id": 1, "impact": "qualifies", "rationale": "Short-term only.",
         "cited_sentence_ids": ["pa:p1_s1", "pd:p2_s3"], "insufficient_evidence": False, "relationship_id": 11},
        # From an earlier upload (paper pe) — must not appear in Paper D's report.
        {"id": 2, "new_claim_id": 4, "prior_claim_id": 2, "impact": "challenges", "rationale": "old",
         "cited_sentence_ids": ["pb:p1_s1", "pe:p1_s1"], "insufficient_evidence": False, "relationship_id": None},
    ]
    s = build_impact_summary(PAPERS[2], CLAIMS, impacts, SENT, PAPERS)
    assert [i["id"] for i in s["impacts"]] == [1]
    assert s["counts"]["qualifies"] == 1 and s["counts"]["challenges"] == 0
    # B and E are not addressed by D (E's impact on B belongs to E's report)
    assert {c["id"] for c in s["not_addressed"]} == {2, 4}
    assert [q["id"] for q in s["impacts"][0]["cited"]] == ["pa:p1_s1", "pd:p2_s3"]


def test_impact_summary_hides_citations_when_insufficient():
    impacts = [{"id": 5, "new_claim_id": 3, "prior_claim_id": 1, "impact": "leaves_unresolved",
                "rationale": "Insufficient evidence", "cited_sentence_ids": ["pa:p1_s1"],
                "insufficient_evidence": True}]
    s = build_impact_summary(PAPERS[2], CLAIMS, impacts, SENT, PAPERS)
    assert s["impacts"][0]["cited"] == []


def test_ref_order_numbers_papers_by_insertion_then_their_claims():
    from app.graph import ref_order
    papers = [{"id": "b", "created_at": "2026-01-02"}, {"id": "a", "created_at": "2026-01-01"}]
    claims = [{"id": 3, "paper_id": "a"}, {"id": 1, "paper_id": "b"}, {"id": 2, "paper_id": "a"}]
    assert ref_order(papers, claims) == ["paper:a", "claim:2", "claim:3", "paper:b", "claim:1"]


def test_build_graph_attaches_refs_only_when_known():
    g = build_graph("Q?", PAPERS, CLAIMS, RELS, SENT, refs={"paper:" + PAPERS[0]["id"]: 7})
    data = {n["data"]["id"]: n["data"] for n in g["nodes"]}
    assert data["paper:" + PAPERS[0]["id"]]["ref"] == 7
    assert "ref" not in data["q"]
