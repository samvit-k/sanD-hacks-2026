"""Research assistant: answer validation (pure) and the grounded chat flow (embedded Postgres + FakeLLM)."""
import pytest
from fastapi.testclient import TestClient

from app import chat, db, main, pipeline
from app.config import settings
from app.contracts import ChatAnswer

from .fake_llm import FakeLLM
from .test_pipeline import PAPERS, pdf, seed
from .conftest import SESSION_HEADERS  # noqa: E402

SHOWN = {
    "paper-a:p1_s3": "Tutor students' test scores rose 25 percent after 12 weeks.",
    "paper-b:p1_s3": "Test scores showed no significant improvement after 2 weeks.",
}
TOKENS = {"q", "paper:paper-a", "paper:paper-b", "claim:1", "claim:2", "rel:1"}

def check(answer, cited=(), insufficient=False):
    return chat.validate_answer(ChatAnswer(answer=answer, cited_sentence_ids=list(cited),
                                           insufficient_evidence=insufficient), SHOWN, TOKENS)

# ---------- citations required for research statements ----------
@pytest.mark.parametrize("answer", [
    "[[claim:1]] reports that tutoring raised scores by 25 percent.",
    "Personalized tutoring benefits students.",                      # no keywords, no digits
    "I can confirm that personalized tutoring improves student outcomes.",
    "I can confirm tutoring improves outcomes.",
    "Please note that the intervention significantly increased performance.",
    "Please note that performance increased.",
    "It is unclear why the treatment works, but the results demonstrate an improvement.",
    "There is no evidence about retention and tutoring improves scores.",
    "You can select a connection in the graph because tutoring improves scores.",
])
def test_research_statements_without_citations_are_withheld(answer):
    out = check(answer)
    assert out["guardrail"] == chat.GUARDRAIL_UNCITED
    assert out["cited_sentence_ids"] == [] and answer not in out["content"]

@pytest.mark.parametrize("answer", [
    "Personalized tutoring benefits students.",
    "Personalized tutoring benefits students, but there is insufficient evidence about retention.",
])
def test_insufficient_flag_cannot_exempt_research_statements(answer):
    assert check(answer, insufficient=True)["guardrail"] == chat.GUARDRAIL_UNCITED

def test_cited_answer_passes():
    out = check("[[claim:1]] reports a 25 percent gain after 12 weeks, while [[claim:2]] found no significant "
                "change after 2 weeks.", cited=["paper-a:p1_s3", "paper-b:p1_s3"])
    assert out["guardrail"] is None
    assert out["cited_sentence_ids"] == ["paper-a:p1_s3", "paper-b:p1_s3"]

@pytest.mark.parametrize("answer", [
    "There is insufficient evidence to answer that question.",
    "There is insufficient evidence about Node 7.",
    "There is insufficient evidence about [[claim:2]].",
    "The library does not contain evidence about long-term retention.",
])
def test_insufficient_evidence_reply_may_be_citation_free(answer):
    out = check(answer, insufficient=True)
    assert out["guardrail"] is None and out["content"] == answer and out["insufficient_evidence"] is True

@pytest.mark.parametrize("answer", [
    "Hello! How can I help?",
    "I can help you inspect Node 7.",
    "Could you clarify which paper you mean?",
    "You can select a connection in the graph.",
    "Thanks! You can select a connection in the graph.",
])
def test_conversational_replies_are_not_rejected(answer):
    assert check(answer)["guardrail"] is None

def test_invalid_citation_id_withholds_answer():
    out = check("[[claim:1]] reports a gain.", cited=["paper-a:p1_s3", "paper-a:p9_s9"])
    assert out["guardrail"] == chat.GUARDRAIL_CITATIONS and out["cited_sentence_ids"] == []

def test_invented_graph_reference_withholds_answer():
    assert check("[[claim:99]] reports a gain.", cited=["paper-a:p1_s3"])["guardrail"] == chat.GUARDRAIL_REFERENCES
    assert check("[[claim:one]] reports a gain.", cited=["paper-a:p1_s3"])["guardrail"] == chat.GUARDRAIL_REFERENCES

def test_inline_sentence_ids_are_validated_cited_and_removed_from_the_text():
    out = check("[[claim:1]] reports a 25 percent gain [paper-a:p1_s3], unlike [[claim:2]] (paper-b:p1_s3).")
    assert out["guardrail"] is None
    assert out["cited_sentence_ids"] == ["paper-a:p1_s3", "paper-b:p1_s3"]
    assert out["content"] == "[[claim:1]] reports a 25 percent gain, unlike [[claim:2]]."
    bad = check("[[claim:1]] reports a gain [paper-a:p9_s9].", cited=["paper-a:p1_s3"])
    assert bad["guardrail"] == chat.GUARDRAIL_CITATIONS

# ---------- direct quotations ----------
def test_invented_direct_quotation_is_withheld_even_with_valid_citations():
    out = check('The paper states "tutoring doubled long-term retention for all pupils".', cited=["paper-a:p1_s3"])
    assert out["guardrail"] == chat.GUARDRAIL_QUOTE
    assert "doubled" not in out["content"]

def test_verbatim_quotation_is_supported_and_cited():
    out = check('[[claim:1]] is based on the sentence “test scores rose 25 percent after 12 weeks”.')
    assert out["guardrail"] is None
    assert out["cited_sentence_ids"] == ["paper-a:p1_s3"]  # the quoted sentence itself

def test_short_quoted_terms_are_ordinary_language():
    out = check('This pair is labelled "apparent contradiction" by the model.', cited=["paper-a:p1_s3"])
    assert out["guardrail"] is None

# ---------- end to end ----------
@pytest.fixture
def client(fake_llm):
    seed(fake_llm)
    pipeline.graph_payload()  # assigns node numbers, as the UI would
    yield TestClient(main.app, headers=SESSION_HEADERS), fake_llm

def test_chat_answer_is_grounded_stored_and_resolves_references(client):
    c, fake = client
    r = c.post("/api/chat", json={"message": "Do AI tutors raise test scores?"})
    assert r.status_code == 200, r.text
    bot = r.json()["assistant"]
    assert bot["guardrail"] is None and bot["cited"]
    with db.connect() as conn:
        stored = db.sentences_by_ids(conn, [q["id"] for q in bot["cited"]])
    for q in bot["cited"]:
        assert q["text"] == stored[q["id"]]["text"] and q["paper_title"]  # rebuilt from the DB
    claim_refs = [v for v in bot["references"].values() if v["kind"] == "claim"]
    assert claim_refs and all(isinstance(v["ref"], int) and not v["missing"] for v in claim_refs)
    assert "CENTRAL RESEARCH QUESTION" in fake.prompts[-1]
    hist = c.get("/api/chat/messages").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["cited"] == bot["cited"]

def test_context_and_node_mentions_are_retrieved(client):
    c, fake = client
    g = c.get("/api/graph").json()
    rel = next(iter(g["relationships"].values()))
    c.post("/api/chat", json={"message": "Why are these connected?", "context": {"kind": "relationship", "id": rel["id"]}})
    prompt = fake.prompts[-1]
    assert f"[[rel:{rel['id']}]]" in prompt.split("ITEMS THE USER SELECTED", 1)[1]
    refs = {n["data"]["id"]: n["data"]["ref"] for n in g["nodes"] if n["data"].get("ref")}
    claim_node = next(k for k in refs if k.startswith("claim:"))
    c.post("/api/chat", json={"message": f"What does Node {refs[claim_node]} say?"})
    assert f"[[{claim_node}]]" in fake.prompts[-1].split("ITEMS THE USER SELECTED", 1)[1]

def test_withheld_answer_is_stored_without_citations(client):
    c, fake = client
    fake.chat_answer = ChatAnswer(answer="Personalized tutoring benefits students.", cited_sentence_ids=[],
                                  insufficient_evidence=False)
    bot = c.post("/api/chat", json={"message": "Does tutoring help?"}).json()["assistant"]
    assert bot["guardrail"] == chat.GUARDRAIL_UNCITED and bot["cited"] == []
    assert "benefits students" not in bot["content"]

def test_session_id_echo_must_match_the_header(client):
    """Isolation itself is per session schema (tests/test_sessions.py); a body/query session_id is only an echo."""
    c, _ = client
    assert c.post("/api/chat", json={"message": "Do tutors help?", "session_id": "aaaaaaaaaaaa"}).status_code == 422
    assert c.get("/api/chat/messages", params={"session_id": "aaaaaaaaaaaa"}).status_code == 422
    assert c.post("/api/chat", json={"message": "Do tutors help?", "session_id": SESSION_HEADERS["X-Session-Id"]}).status_code == 200
    assert len(c.get("/api/chat/messages").json()["messages"]) == 2

def test_model_outage_stores_nothing(client):
    c, fake = client
    fake.fail_chat = True
    r = c.post("/api/chat", json={"message": "Do tutors help?"})
    assert r.status_code == 503 and "not saved" in r.json()["detail"]
    assert c.get("/api/chat/messages").json()["messages"] == []

def test_chat_requires_question_and_valid_input(client, monkeypatch):
    c, _ = client
    assert c.post("/api/chat", json={"message": "   "}).status_code == 422
    assert c.post("/api/chat", json={"message": "x", "context": {"kind": "bogus"}}).status_code == 422
    with db.connect() as conn:
        conn.execute("TRUNCATE workspace")
    monkeypatch.setattr(settings, "research_question", None)
    assert c.post("/api/chat", json={"message": "Do tutors help?"}).status_code == 409

def test_reset_clears_the_conversation(client):
    c, _ = client
    c.post("/api/chat", json={"message": "Do tutors help?"})
    with db.connect() as conn:
        db.delete_all(conn)
    assert c.get("/api/chat/messages").json()["messages"] == []

# ---------- duplicate uploads ----------
def _snapshot():
    with db.connect() as conn:
        return {
            "counts": db.analysis_counts(conn),
            "papers": {p["id"]: (p["title"], p["is_new"], p["access_level"], p["created_at"])
                       for p in db.all_papers(conn)},
            "claims": sorted(c["id"] for c in db.all_claims(conn)),
            "relationships": sorted((r["id"], r["label"]) for r in db.all_relationships(conn)),
            "impacts": conn.execute("SELECT count(*) AS n FROM impacts").fetchone()["n"],
            "sentences": conn.execute("SELECT count(*) AS n FROM sentences").fetchone()["n"],
        }

def test_duplicate_pdf_is_rejected_and_leaves_the_database_unchanged(client):
    c, fake = client
    data = pdf(PAPERS["paper-a"][1] + "\nAdditional results: retention was measured at 3 months.")
    first = c.post("/api/papers", files={"file": ("original.pdf", data, "application/pdf")},
                   data={"mode": "library", "title": "Original title"})
    assert first.status_code == 200, first.text
    paper_id = first.json()["paper"]["id"]
    before = _snapshot()
    calls = len(fake.prompts)

    again = c.post("/api/papers", files={"file": ("renamed-copy.pdf", data, "application/pdf")},
                   data={"mode": "evaluate", "title": "A different title"})

    assert again.status_code == 409
    detail = again.json()["detail"]
    assert detail["code"] == "duplicate" and detail["paper_id"] == paper_id and detail["title"] == "Original title"
    assert _snapshot() == before                        # counts and every id unchanged
    assert before["papers"][paper_id][:2] == ("Original title", False)  # metadata and is_new untouched
    assert len(fake.prompts) == calls                   # no model calls were made

# ---------- upload reconciliation (timeout / network recovery) ----------
def test_lookup_identifies_the_exact_pdf_and_its_analysis_status(client):
    import hashlib
    c, _ = client
    data = pdf(PAPERS["paper-b"][1] + "\nA unique closing sentence for this upload.")
    other = pdf(PAPERS["paper-c"][1] + "\nA different unrelated upload.")
    digest = hashlib.sha256(data).hexdigest()
    assert c.get("/api/papers/lookup", params={"sha256": digest}).json() == {"found": False, "status": None, "paper": None}
    # An unrelated paper appearing must not count as this upload.
    assert c.post("/api/papers", files={"file": ("o.pdf", other, "application/pdf")}, data={"mode": "library"}).status_code == 200
    assert c.get("/api/papers/lookup", params={"sha256": digest}).json()["found"] is False
    r = c.post("/api/papers", files={"file": ("b.pdf", data, "application/pdf")}, data={"mode": "library"})
    found = c.get("/api/papers/lookup", params={"sha256": digest}).json()
    assert found["found"] is True and found["status"] == "complete" and found["paper"]["id"] == r.json()["paper"]["id"]
    assert c.get("/api/papers/lookup", params={"sha256": "nothex"}).status_code == 422

def test_upload_is_marked_processing_until_analysis_finishes(client, monkeypatch):
    import hashlib
    c, fake = client
    data = pdf(PAPERS["paper-c"][1] + "\nYet another distinct upload text.")
    seen = {}
    real_compare = pipeline.compare_claims

    def spy(llm, claim_ids, question=None, refresh=False):
        seen.update(c.get("/api/papers/lookup", params={"sha256": hashlib.sha256(data).hexdigest()}).json())
        return real_compare(llm, claim_ids, question, refresh)

    monkeypatch.setattr(pipeline, "compare_claims", spy)
    assert c.post("/api/papers", files={"file": ("c.pdf", data, "application/pdf")}, data={"mode": "library"}).status_code == 200
    assert seen["found"] is True and seen["status"] == "processing"   # mid-analysis
    assert c.get("/api/papers/lookup", params={"sha256": hashlib.sha256(data).hexdigest()}).json()["status"] == "complete"