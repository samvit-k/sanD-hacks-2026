"""End-to-end pipeline against embedded Postgres + pgvector with a fake Gemini."""
import fitz
import pytest

from app import db, pipeline
from app.config import settings
from app.ingest import NoExtractableTextError

from .fake_llm import FakeLLM

PAPERS = {
    "paper-a": ("AI tutor RCT, 12 weeks",
                "Abstract\nWe tested an AI tutor in grade 8 classrooms.\n3. Results\n"
                "Tutor students' test scores rose 25 percent after 12 weeks.\n"
                "The control group did not change.\n4. Conclusion\nTutoring helped."),
    "paper-b": ("Short AI tutor trial, 2 weeks",
                "Abstract\nA brief trial of an AI tutor.\nResults\n"
                "Test scores showed no significant improvement after 2 weeks.\nDiscussion\nThe trial was short."),
    "paper-c": ("AI tutors for low performers",
                "Results\nStudents with low prior test scores gained 30 percent after 10 weeks.\n"
                "Conclusion\nGains were largest for low performers."),
}
PAPER_D = ("Retention of AI tutor gains",
           "Abstract\nWe followed tutored students for six months.\nResults\n"
           "Test score gains after 12 weeks of tutoring were not retained after 6 months.\n")

def pdf(text):
    doc = fitz.open()
    doc.new_page().insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data

@pytest.fixture
def setup(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)  # bag-of-words fake embeddings are coarse
    monkeypatch.setattr(settings, "top_k", 2)
    pipeline._question_vecs.clear()

def seed(llm):
    ids = []
    for pid, (title, text) in PAPERS.items():
        pipeline.add_paper(paper_id=pid, title=title, pdf_bytes=pdf(text))
        claim_ids, _ = pipeline.extract_claims(llm, pid)
        ids += claim_ids
    pipeline.compare_claims(llm, ids)
    return ids

def test_seed_and_new_paper_end_to_end(setup):
    llm = FakeLLM()
    claim_ids = seed(llm)
    assert len(claim_ids) == 3
    with db.connect() as conn:
        rels = db.all_relationships(conn)
        assert rels and all(r["claim_a"] < r["claim_b"] for r in rels)
        assert all(not r["insufficient_evidence"] for r in rels)
        # condition diff is descriptive; the label came from the (fake) model, not from the diff
        assert any(isinstance(r["condition_diff"]["duration"], list) for r in rels)

    summary = pipeline.process_new_paper(llm, pdf(PAPER_D[1]), title=PAPER_D[0])
    assert summary["paper"]["is_new"] and summary["new_claims"]
    assert summary["impacts"], "Paper D should be compared with earlier claims"
    for item in summary["impacts"]:
        assert item["impact"] == "qualifies" and not item["insufficient_evidence"]
        papers_cited = {q["paper_id"] for q in item["cited"]}
        assert papers_cited == {item["prior_claim"]["paper_id"], summary["paper"]["id"]}
    total_prior = 3
    assert len({i["prior_claim"]["id"] for i in summary["impacts"]}) + summary["counts"]["not_addressed"] == total_prior
    # impact prompt receives the relationship only as context
    assert "context only" in llm.prompts[-1] and "EARLIER FINDING" in llm.prompts[-1]

    with db.connect() as conn:
        assert all(not v for v in db.integrity_violations(conn).values())

    g = pipeline.graph_payload()
    types = {n["data"]["type"] for n in g["nodes"]}
    assert {"question", "paper", "claim"} <= types
    assert len([n for n in g["nodes"] if n["data"]["type"] == "paper"]) == 4

def test_fabricated_citations_become_insufficient_evidence(setup):
    llm = FakeLLM(fabricate=True)
    seed(llm)
    summary = pipeline.process_new_paper(llm, pdf(PAPER_D[1]), title=PAPER_D[0])
    with db.connect() as conn:
        rels = db.all_relationships(conn)
        assert rels and all(r["label"] == "unresolved" and r["insufficient_evidence"] for r in rels)
        assert all(r["cited_sentence_ids"] == [] and "zz-fake" not in r["explanation"] for r in rels)
        assert all(not v for v in db.integrity_violations(conn).values())
    assert all(i["impact"] == "leaves_unresolved" and i["cited"] == [] for i in summary["impacts"])
    assert "Durations differ" not in str(summary)  # unsupported model text never shown

def test_model_failure_is_recorded_as_unresolved(setup):
    llm = FakeLLM(fail_pairs=True)
    seed(llm)
    with db.connect() as conn:
        rels = db.all_relationships(conn)
    assert rels and all(r["label"] == "unresolved" and r["explanation"] == pipeline.FAILED_PAIR for r in rels)

def test_reupload_is_rejected_unless_replace_is_explicit(setup):
    llm = FakeLLM()
    seed(llm)
    same_file = pdf(PAPER_D[1])
    s1 = pipeline.process_new_paper(llm, same_file, title=PAPER_D[0])
    with pytest.raises(pipeline.DuplicatePaperError):
        pipeline.process_new_paper(llm, same_file, title="Other title")
    with pytest.raises(pipeline.DuplicatePaperError):
        pipeline.add_library_paper(llm, same_file)
    s2 = pipeline.process_new_paper(llm, same_file, title=PAPER_D[0], replace=True)  # seed CLI only
    assert s1["paper"]["id"] == s2["paper"]["id"]
    with db.connect() as conn:
        assert len([p for p in db.all_papers(conn) if p["is_new"]]) == 1

def test_blank_pdf_rejected_before_anything_is_stored(setup):
    blank = fitz.open()
    blank.new_page()
    with pytest.raises(NoExtractableTextError):
        pipeline.process_new_paper(FakeLLM(), blank.tobytes(), title="Scanned")
    with db.connect() as conn:
        assert db.all_papers(conn) == []

def _refs(graph):
    return {n["data"]["id"]: n["data"]["ref"] for n in graph["nodes"] if n["data"]["type"] in ("paper", "claim")}

def test_node_numbers_are_stable_and_append_only(setup):
    llm = FakeLLM()
    seed(llm)
    first = _refs(pipeline.graph_payload())
    assert sorted(first.values()) == list(range(1, len(first) + 1))  # 1..N, no gaps
    assert first["paper:paper-a"] == 1 and first[f"claim:{min(int(k[6:]) for k in first if k.startswith('claim:'))}"] == 2
    assert _refs(pipeline.graph_payload()) == first  # a refresh never renumbers

    # A later claim on an EARLIER paper gets the next number; nothing else moves.
    with db.connect() as conn:
        sid = conn.execute("SELECT id FROM sentences WHERE paper_id = 'paper-a' LIMIT 1").fetchone()["id"]
        new_claim = conn.execute("INSERT INTO claims (paper_id, text, sentence_ids) VALUES ('paper-a', %s, %s) "
                                 "RETURNING id", ("Another finding.", [sid])).fetchone()["id"]
    pipeline.add_paper(paper_id="paper-d", title=PAPER_D[0], pdf_bytes=pdf(PAPER_D[1]), is_new=True)
    pipeline.extract_claims(llm, "paper-d")
    after = _refs(pipeline.graph_payload())
    assert {k: after[k] for k in first} == first
    assert after[f"claim:{new_claim}"] == len(first) + 1
    assert after["paper:paper-d"] == len(first) + 2
    assert len(set(after.values())) == len(after)

    # Only an explicit reset clears the numbering.
    with db.connect() as conn:
        db.delete_all(conn)
    assert _refs(pipeline.graph_payload()) == {}