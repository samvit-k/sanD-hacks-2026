"""Stage C: Gemini-proposed central research question, explicit confirmation, and safe question changes.
All tests use the isolated pgserver test database (clean_db), never the real local research dataset."""
import ast
import inspect

import pytest

from app import db, question as questions
from app.contracts import QuestionProposal

from .conftest import DEFAULT_TEST_QUESTION
from .test_pipeline import PAPER_D
from .test_workspace import USER_Q, api, build_library, counts, upload  # noqa: F401  (fixture)

INTEREST = "I'm interested in how AI tutoring affects student learning outcomes."
NEW_Q = "How does AI-assisted tutoring influence student learning outcomes compared with traditional instruction?"
EXTRACT = "You are extracting testable"

def stored_question():
    with db.connect() as conn:
        return db.get_question(conn)

def snapshot():
    """Papers, sentence text, node numbers and chat rows: what keep_papers must never destroy."""
    with db.connect() as conn:
        papers = {r["id"]: (r["title"], r["is_new"], r["content_sha256"]) for r in conn.execute("SELECT * FROM papers")}
        sentences = {r["id"]: r["text"] for r in conn.execute("SELECT id, text FROM sentences")}
        refs = db.node_ref_map(conn)
        msgs = conn.execute("SELECT count(*) AS n FROM chat_messages").fetchone()["n"]
    return papers, sentences, refs, msgs

def full_project(c):
    """Library + one evaluated paper + node numbers + one chat exchange."""
    build_library(c)
    assert upload(c, PAPER_D[1], PAPER_D[0], mode="evaluate").status_code == 200
    c.get("/api/graph")
    assert c.post("/api/chat", json={"message": "Where do the papers disagree?"}).status_code == 200
    n = counts()
    assert n["papers"] and n["claims"] and n["relationships"] and n["impacts"]

def propose(q, rationale="Names the intervention, outcome and comparison.", interest=INTEREST, previous=()):
    return questions.validate_proposal(QuestionProposal(question=q, rationale=rationale), previous, interest)

# ---------- unit: proposal rules (no database, no Gemini) ----------
def test_validate_proposal_interest_is_optional_for_existing_callers():
    assert propose(NEW_Q, interest="")["question"] == NEW_Q  # positional (raw, previous) still works
    assert questions.validate_proposal(QuestionProposal(question=NEW_Q, rationale="r"))["question"] == NEW_Q

def test_proposal_prompt_keeps_interest_as_delimited_data_and_forbids_findings():
    p = questions.build_proposal_prompt(INTEREST, ["Earlier suggestion?"])
    assert f"<<<\n{INTEREST}\n>>>" in p and "Earlier suggestion?" in p
    assert "do not state, summarize or imply any finding" in p and "Do not narrow the scope" in p

@pytest.mark.parametrize("bad", [
    "Studies show AI tutoring improves learning outcomes, but how much does it help students?",
    "Why did student learning outcomes improve by 25% with AI tutoring?",
    "How did the tutoring trial by Smith et al. measure student learning outcomes?",
    "AI tutoring improves student learning outcomes.",   # a statement, not a question
    "Does it work?",                                     # too short to compare evidence
])
def test_rejects_findings_statistics_citations_statements_and_vague_questions(bad):
    with pytest.raises(questions.ProposalRejectedError):
        propose(bad)

@pytest.mark.parametrize("ok", [
    NEW_Q,
    "Is AI tutoring associated with different student learning outcomes than human tutoring?",
    "How do the effects of AI tutoring on student learning outcomes compare with those of classroom instruction?",
    "To what extent are AI tutoring systems associated with changes in how students learn?",  # different wording
])
def test_accepts_comparisons_and_reworded_questions(ok):
    assert propose(ok)["question"] == ok

@pytest.mark.parametrize("narrowed, term", [
    ("How does AI tutoring influence learning outcomes of high school students?", "high school"),
    ("How has AI tutoring influenced student learning outcomes in Kenya?", "Kenya"),
    ("How has AI tutoring influenced student learning outcomes since 2020?", "2020"),
    ("How does AI tutoring affect learning outcomes for students aged 8-12?", "aged 8"),
    ("How does AI tutoring affect 5th-grade students' learning outcomes?", "5th-grade"),
    ("How has AI tutoring affected student learning outcomes over the past decade?", "past decade"),
    ("How does ChatGPT-based tutoring affect student learning outcomes?", "ChatGPT-based"),
    ("How does AI tutoring affect learning outcomes of rural students during the pandemic?", "rural"),
])
def test_rejects_restrictions_gemini_invented(narrowed, term):
    with pytest.raises(questions.ProposalRejectedError, match="restrictions you did not mention") as exc:
        propose(narrowed)
    assert term in str(exc.value)

def test_allows_dates_populations_and_places_the_user_supplied():
    interest = "AI tutoring for high school students in Kenya since 2020, ages 14-18 in rural schools"
    q = ("How has AI tutoring influenced learning outcomes of rural high school students aged 14-18 in Kenya "
         "since 2020?")
    assert propose(q, interest=interest)["question"] == q
    assert questions.unsupported_restrictions(q, interest) == []

def test_restriction_check_does_not_claim_semantic_completeness():
    # Subtle narrowing that no pattern can recognise is left to the researcher's review (documented).
    doc = inspect.getsource(questions)
    assert "NOT a semantic check" in doc
    assert propose("How does AI tutoring influence student learning outcomes in mathematics?")  # not detected

def test_rationale_with_invented_evidence_is_dropped_not_shown():
    out = propose(NEW_Q, rationale="Research shows tutoring raises scores by 20%.")
    assert out["question"] == NEW_Q and out["rationale"] is None

# ---------- POST /api/question/propose ----------
def test_propose_returns_a_question_and_writes_nothing(api):
    c, fake = api
    before = c.get("/api/workspace").json()
    r = c.post("/api/question/propose", json={"interest": INTEREST})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interest"] == INTEREST and body["source"] == "gemini" and body["saved"] is False
    assert body["proposal"]["question"].startswith("How does AI-assisted tutoring") and body["proposal"]["rationale"]
    assert fake.temperatures[-1] == questions.PROPOSAL_TEMPERATURE
    assert stored_question() == DEFAULT_TEST_QUESTION and c.get("/api/workspace").json() == before  # never auto-finalized

def test_regenerate_sends_previous_suggestions_and_gets_a_different_one(api):
    c, fake = api
    first = c.post("/api/question/propose", json={"interest": INTEREST}).json()["proposal"]["question"]
    second = c.post("/api/question/propose", json={"interest": INTEREST, "previous": [first]}).json()
    assert second["proposal"]["question"] != first
    assert "Already suggested" in fake.prompts[-1] and first in fake.prompts[-1]
    assert stored_question() == DEFAULT_TEST_QUESTION

def test_rule_breaking_suggestion_is_retried_once_then_reported(api):
    c, fake = api
    fake.proposals = [QuestionProposal(question="Studies show AI tutors help; by how much do they help students?",
                                       rationale="r"),
                      QuestionProposal(question=NEW_Q, rationale="Focuses on a comparison.")]
    r = c.post("/api/question/propose", json={"interest": INTEREST})
    assert r.status_code == 200 and r.json()["proposal"]["question"] == NEW_Q
    assert "previous suggestion was rejected" in fake.prompts[-1]
    calls = len(fake.prompts)
    fake.proposals = [QuestionProposal(question=NEW_Q, rationale="r")] * 2
    r = c.post("/api/question/propose", json={"interest": INTEREST, "previous": [NEW_Q]})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "proposal_rejected"
    assert len(fake.prompts) - calls == questions.MAX_ATTEMPTS  # bounded paid calls
    assert stored_question() == DEFAULT_TEST_QUESTION

def test_gemini_failure_is_a_clear_error_and_nothing_is_saved(api):
    c, fake = api
    fake.fail_proposal = True
    r = c.post("/api/question/propose", json={"interest": INTEREST})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "model_unavailable"
    assert "write the question yourself" in r.json()["detail"]["message"]
    assert stored_question() == DEFAULT_TEST_QUESTION

def test_propose_validates_the_interest(api):
    c, _ = api
    assert c.post("/api/question/propose", json={"interest": "  ai "}).status_code == 422
    assert c.post("/api/question/propose", json={"interest": "x " * 600}).status_code == 422

# ---------- confirmation: only PUT /api/workspace changes the question ----------
def test_confirming_an_edited_or_manual_question_saves_exactly_that_text(api):
    c, fake = api
    proposal = c.post("/api/question/propose", json={"interest": INTEREST}).json()["proposal"]["question"]
    edited = proposal.replace("traditional instruction", "human tutoring")
    assert edited != proposal
    r = c.put("/api/workspace", json={"question": edited})
    assert r.status_code == 200 and stored_question() == edited
    graph = c.get("/api/graph").json()
    assert graph["question"] == edited and any(n["data"]["id"] == "q" for n in graph["nodes"])
    calls = len(fake.prompts)
    assert c.put("/api/workspace", json={"question": USER_Q}).status_code == 200  # manual entry: no Gemini
    assert stored_question() == USER_Q and len(fake.prompts) == calls

# ---------- changing the question of an existing project ----------
def test_question_change_needs_an_explicit_choice_and_reset_is_destructive_only_when_chosen(api):
    c, _ = api
    full_project(c)
    before, n = snapshot(), counts()
    assert before[2] and before[3]  # node numbers and chat exist

    # 1-2. No choice -> 409, nothing changes.
    r = c.put("/api/workspace", json={"question": NEW_Q})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "question_conflict" and set(d["options"]) == {"keep_papers", "reset"}
    assert d["current_question"] == DEFAULT_TEST_QUESTION and d["counts"]["claims"] == n["claims"]
    assert c.put("/api/workspace", json={"question": NEW_Q, "reset": True, "keep_papers": True}).status_code == 422
    assert snapshot() == before and counts() == n and stored_question() == DEFAULT_TEST_QUESTION

    # 6. Proposals never reset or change anything, even for a project with data.
    assert c.post("/api/question/propose", json={"interest": INTEREST}).status_code == 200
    assert snapshot() == before and counts() == n and stored_question() == DEFAULT_TEST_QUESTION

    # 3-5. Explicit reset: everything derived from the old project is cleared and the new question saved.
    r = c.put("/api/workspace", json={"question": NEW_Q, "reset": True})
    assert r.status_code == 200 and r.json()["reset"] is True and r.json()["analysis_cleared"] is False
    assert stored_question() == NEW_Q and c.get("/api/workspace").json()["question"] == NEW_Q
    after = counts()
    assert all(after[k] == 0 for k in ("papers", "claims", "relationships", "impacts", "pending_papers"))
    papers, sentences, refs, msgs = snapshot()
    assert papers == {} and sentences == {} and refs == {} and msgs == 0
    assert c.get("/api/graph").json()["question"] == NEW_Q

def test_keep_papers_clears_only_analysis_and_preserves_papers_text_refs_and_chat(api):
    c, _ = api
    full_project(c)
    papers, sentences, refs, msgs = snapshot()
    r = c.put("/api/workspace", json={"question": NEW_Q, "keep_papers": True})
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["question"] == NEW_Q and w["analysis_cleared"] and not w["reset"]
    assert w["counts"]["claims"] == w["counts"]["relationships"] == w["counts"]["impacts"] == 0
    assert [p["is_new"] for p in w["pending_papers"]] == [False] * (len(papers) - 1) + [True]  # library first
    assert snapshot() == (papers, sentences, refs, msgs)  # nothing destroyed or renumbered
    graph = c.get("/api/graph").json()
    assert {f"paper:{pid}" for pid in papers} <= {n["data"]["id"] for n in graph["nodes"]}
    assert all(p["analysis_status"] == "pending" for p in graph["papers"].values())
    # New uploads wait until the existing papers are re-analyzed (no partial analyses mixed together).
    r = upload(c, "Results: a fresh paper found 9 percent gains after 3 weeks.", "Fresh", mode="library")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pending_analysis"

def test_deliberate_reanalysis_keeps_paper_numbers_and_never_reuses_retired_ones(api):
    c, fake = api
    full_project(c)
    old = {n["data"]["id"]: n["data"]["ref"] for n in c.get("/api/graph").json()["nodes"] if n["data"].get("ref")}
    c.put("/api/workspace", json={"question": NEW_Q, "keep_papers": True})
    pending = c.get("/api/workspace").json()["pending_papers"]
    assert c.post(f"/api/papers/{pending[-1]['id']}/analyze").status_code == 409  # evaluated paper needs the library
    for p in pending:
        r = c.post(f"/api/papers/{p['id']}/analyze")
        assert r.status_code == 200, r.text
        assert r.json()["question"] == NEW_Q
    assert any(EXTRACT in p and NEW_Q in p for p in fake.prompts)  # analysis ran under the confirmed question
    w = c.get("/api/workspace").json()
    assert w["pending_papers"] == [] and w["counts"]["claims"] > 0 and w["counts"]["impacts"] > 0
    assert c.post(f"/api/papers/{pending[0]['id']}/analyze").json()["detail"]["code"] == "already_analyzed"
    new = {n["data"]["id"]: n["data"]["ref"] for n in c.get("/api/graph").json()["nodes"] if n["data"].get("ref")}
    assert all(new[k] == v for k, v in old.items() if k.startswith("paper:"))  # papers keep their Node numbers
    retired = {v for k, v in old.items() if k.startswith("claim:")}
    assert retired and not retired & {v for k, v in new.items() if k.startswith("claim:")}
    assert c.get("/api/checks").json()["ok"]

def test_reanalysis_failure_keeps_the_paper_and_its_text_pending(api):
    c, fake = api
    build_library(c)
    c.put("/api/workspace", json={"question": NEW_Q, "keep_papers": True})
    pid = c.get("/api/workspace").json()["pending_papers"][0]["id"]
    papers, sentences, _, _ = snapshot()
    fake.fail_extraction = True
    r = c.post(f"/api/papers/{pid}/analyze")
    assert r.status_code == 503 and "still awaits re-analysis" in r.json()["detail"]
    assert snapshot()[:2] == (papers, sentences)
    first = c.get("/api/workspace").json()["pending_papers"][0]
    assert first["id"] == pid and first["status"] == "pending"

# ---------- chatbot compatibility ----------
def test_chat_uses_the_confirmed_question_and_keeps_earlier_turns_out_of_the_model(api):
    c, fake = api
    build_library(c)
    assert c.post("/api/chat", json={"message": "Where do the papers disagree?"}).status_code == 200
    c.put("/api/workspace", json={"question": NEW_Q, "keep_papers": True})
    for p in c.get("/api/workspace").json()["pending_papers"]:
        assert c.post(f"/api/papers/{p['id']}/analyze").status_code == 200
    r = c.post("/api/chat", json={"message": "Which findings support each other?"})
    assert r.status_code == 200, r.text
    prompt = [p for p in fake.prompts if "Which findings support each other?" in p][-1]
    assert NEW_Q in prompt and "Where do the papers disagree?" not in prompt
    msgs = c.get("/api/chat/messages").json()["messages"]
    assert [m["question"] for m in msgs] == [DEFAULT_TEST_QUESTION] * 2 + [NEW_Q] * 2

def test_question_module_has_no_dependency_on_ingestion_or_the_database():
    tree = ast.parse(inspect.getsource(questions))
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"db", "ingest", "pipeline", "chat"}, imported
# ---------- POST /api/question/propose-from-papers (paper-first onboarding) ----------
from .test_pipeline import PAPERS, pdf  # noqa: E402

def _pdf_files(n=2):
    return [("files", (f"p{i}.pdf", pdf(text), "application/pdf"))
            for i, (_, text) in enumerate(list(PAPERS.values())[:n])]

def test_propose_from_uploaded_pdfs_writes_nothing(api):
    c, fake = api
    before = (stored_question(), counts()["papers"])
    r = c.post("/api/question/propose-from-papers", files=_pdf_files())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposal"]["question"].endswith("?") and body["saved"] is False
    assert body["source"] == "gemini_papers" and len(body["papers"]) == 2
    assert "PAPER 1:" in fake.prompts[-1] and "PAPER 2:" in fake.prompts[-1]
    assert (stored_question(), counts()["papers"]) == before  # PDFs were read, never stored

def test_propose_from_stored_papers_when_no_files_are_sent(api):
    c, fake = api
    build_library(c)
    r = c.post("/api/question/propose-from-papers")
    assert r.status_code == 200, r.text
    assert len(r.json()["papers"]) == len(PAPERS)

def test_propose_from_papers_needs_papers(api):
    c, _ = api
    assert c.post("/api/question/propose-from-papers").status_code == 422

def test_paper_based_suggestion_may_not_copy_statistics_from_the_papers():
    basis = "Students using the tutor scored 25% higher after 12 weeks."
    with pytest.raises(questions.ProposalRejectedError):
        questions.validate_proposal(QuestionProposal(question="Do AI tutors raise test scores by 25% for students?",
                                                     rationale="r"), (), interest=basis, evidence_basis="")
