"""Clearing a project's chat / papers and removing one paper: confirmation, per-project isolation, flagged chat
evidence, and protection against in-flight uploads, re-analysis and Gemini answers (late results never write
deleted content back). Runs on the isolated test database only."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline
from app import llm as llm_mod
from app.config import settings
from app.contracts import ChatAnswer

from .conftest import SESSION_HEADERS, TEST_SESSION
from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, PAPERS, pdf, seed
from .test_sessions import B_PAPER, Q_B, fingerprint, new_session, upload


@pytest.fixture
def env(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    pipeline._question_vecs.clear()
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    seed(fake)
    pipeline.graph_payload()  # assigns node numbers, as the UI would
    yield TestClient(main.app, headers=SESSION_HEADERS), fake
    llm_mod.set_llm(None)


def counts():
    with db.connect() as conn:
        return {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
                for t in ("papers", "sentences", "claims", "relationships", "impacts", "chat_messages",
                          "relationship_reviews")}


def cited_paper(c):
    """Ask once; return the paper id whose sentence the (fake) answer cited."""
    bot = c.post("/api/chat", json={"message": "Do AI tutors raise test scores?"}).json()["assistant"]
    with db.connect() as conn:
        return db.sentences_by_ids(conn, [bot["cited"][0]["id"]])[bot["cited"][0]["id"]]["paper_id"]


# ---------- confirmation ----------
@pytest.mark.parametrize("path", ["/api/chat/messages", "/api/papers", "/api/papers/paper-a"])
def test_every_clear_needs_explicit_confirmation(env, path):
    c, _ = env
    c.post("/api/chat", json={"message": "Do tutors help?"})
    before = counts()
    for params in ({}, {"confirm": "false"}):
        r = c.delete(path, params=params)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "confirmation_required"
    assert counts() == before


def test_clear_requires_a_valid_session(env):
    assert TestClient(main.app).delete("/api/papers", params={"confirm": "true"}).status_code == 400
    r = TestClient(main.app, headers={"X-Session-Id": "aaaaaaaaaaaa"}).delete("/api/papers", params={"confirm": "true"})
    assert r.status_code == 404


# ---------- clear chat ----------
def test_clear_chat_deletes_only_the_conversation(env):
    c, _ = env
    c.post("/api/chat", json={"message": "Do tutors help?"})
    before = counts()
    r = c.delete("/api/chat/messages", params={"confirm": "true"})
    assert r.status_code == 200 and r.json()["deleted"] == 2 and r.json()["session_id"] == TEST_SESSION
    assert c.get("/api/chat/messages").json()["messages"] == []
    after = counts()
    assert after["chat_messages"] == 0
    assert {k: v for k, v in after.items() if k != "chat_messages"} == \
        {k: v for k, v in before.items() if k != "chat_messages"}
    # The conversation continues normally afterwards.
    assert c.post("/api/chat", json={"message": "Do tutors help?"}).status_code == 200


# ---------- clear all papers ----------
def test_clear_papers_deletes_papers_analysis_and_chat_but_keeps_the_question(env):
    c, _ = env
    c.post("/api/chat", json={"message": "Do tutors help?"})
    question = c.get("/api/workspace").json()["question"]
    r = c.delete("/api/papers", params={"confirm": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"]["papers"] == len(PAPERS) and body["deleted"]["chat_messages"] == 2
    assert body["workspace"]["question"] == question
    assert all(v == 0 for v in counts().values())
    assert c.get("/api/workspace").json()["question"] == question
    g = c.get("/api/graph").json()
    assert not g["papers"] and not g["relationships"]
    assert c.get("/api/checks").json()["ok"] is True
    # The same PDFs can be uploaded again (no stale duplicate detection).
    title, text = PAPERS["paper-a"]
    assert c.post("/api/papers", files={"file": ("a.pdf", pdf(text), "application/pdf")},
                  data={"title": title, "mode": "library"}).status_code == 200


def test_clearing_one_project_never_touches_another(env):
    c, _ = env
    c.post("/api/chat", json={"message": "Do tutors help?"})
    sid_b = new_session(Q_B)
    assert upload(sid_b, *B_PAPER).status_code == 200
    assert TestClient(main.app, headers={"X-Session-Id": sid_b}).post(
        "/api/chat", json={"message": "Does spacing help?"}).status_code == 200
    b_before = fingerprint(sid_b)
    assert b_before["papers"] and b_before["chat"]
    assert c.delete("/api/papers/paper-a", params={"confirm": "true"}).status_code == 200
    assert c.delete("/api/chat/messages", params={"confirm": "true"}).status_code == 200
    assert c.delete("/api/papers", params={"confirm": "true"}).status_code == 200
    assert fingerprint(sid_b) == b_before
    # And the reverse: B's paper id is not removable through A.
    assert c.delete(f"/api/papers/{b_before['papers'][0]}", params={"confirm": "true"}).status_code == 404


# ---------- remove one paper ----------
def test_remove_paper_cascades_and_removes_its_reviews(env):
    c, _ = env
    with db.connect() as conn:
        fp = db.paper_footprint(conn, "paper-a")
        other = conn.execute("SELECT id FROM relationships WHERE NOT (claim_a = ANY(%s) OR claim_b = ANY(%s)) LIMIT 1",
                             (fp["claims"], fp["claims"])).fetchone()
    assert fp["relationships"]
    for rid in [fp["relationships"][0]] + ([other["id"]] if other else []):
        assert c.post("/api/reviews", json={"target_kind": "relationship", "target_id": rid,
                                            "proposed_label": "supports", "reason": "check",
                                            "reviewer": "tester"}).status_code == 201
    refs_before = c.get("/api/graph").json()
    r = c.delete("/api/papers/paper-a", params={"confirm": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["claims"] == len(fp["claims"])
    with db.connect() as conn:
        assert not db.get_paper(conn, "paper-a")
        assert not conn.execute("SELECT 1 FROM sentences WHERE paper_id = 'paper-a'").fetchone()
        assert not conn.execute("SELECT 1 FROM claims WHERE id = ANY(%s)", (fp["claims"],)).fetchone()
        reviews = [rv["target_id"] for rv in db.all_reviews(conn)]
    assert fp["relationships"][0] not in reviews
    if other:
        assert other["id"] in reviews  # unrelated reviews are kept
    g = c.get("/api/graph").json()
    assert "paper-a" not in g["papers"] and set(g["papers"]) == {"paper-b", "paper-c"}
    assert c.get("/api/checks").json()["ok"] is True
    # Node numbers of the remaining nodes are unchanged (append-only).
    keep = {n["data"]["id"]: n["data"].get("ref") for n in refs_before["nodes"] if "paper-a" not in n["data"]["id"]}
    now = {n["data"]["id"]: n["data"].get("ref") for n in g["nodes"]}
    assert all(now[k] == v for k, v in keep.items() if k in now)


def test_removing_an_already_removed_paper_returns_404_and_changes_nothing(env):
    c, _ = env
    assert c.delete("/api/papers/paper-a", params={"confirm": "true"}).status_code == 200
    before = counts()
    r = c.delete("/api/papers/paper-a", params={"confirm": "true"})
    assert r.status_code == 404
    assert counts() == before


def test_removing_the_evaluated_paper_clears_its_impact(env):
    c, fake = env
    title, text = PAPER_D
    assert c.post("/api/papers", files={"file": ("d.pdf", pdf(text), "application/pdf")},
                  data={"title": title, "mode": "evaluate"}).status_code == 200
    pid = c.get("/api/impact/latest").json()["paper"]["id"]
    assert counts()["impacts"] > 0
    assert c.delete(f"/api/papers/{pid}", params={"confirm": "true"}).json()["was_evaluated"] is True
    assert counts()["impacts"] == 0
    assert c.get("/api/impact/latest").status_code == 404


def test_removed_evidence_is_flagged_in_chat_and_never_sent_to_the_model(env):
    c, fake = env
    pid = cited_paper(c)                                    # turn 1 cites `pid`
    with db.connect() as conn:
        removed = conn.execute("SELECT id, text FROM sentences WHERE paper_id = %s", (pid,)).fetchall()
    assert removed
    fake.chat_answer = ChatAnswer(answer="I could not find enough evidence for that.", cited_sentence_ids=[],
                                  insufficient_evidence=True)
    c.post("/api/chat", json={"message": "Unrelated small talk question"})  # turn 2 cites nothing
    r = c.delete(f"/api/papers/{pid}", params={"confirm": "true"})
    assert r.status_code == 200 and r.json()["chat_messages_flagged"] == 2
    hist = c.get("/api/chat/messages").json()["messages"]
    assert len(hist) == 4                                    # history is preserved
    assert [m["evidence_removed"] for m in hist] == [True, True, False, False]
    assert hist[1]["removed_evidence"] == [{"paper_id": pid, "title": PAPERS[pid][0]}]
    assert hist[1]["cited"] == []                            # the quote no longer exists, so none is shown

    # The next Gemini prompt: the flagged turn is not in the conversation context, and none of the removed
    # paper's sentence ids, sentence text or node token appears anywhere (retrieval reads only live evidence).
    fake.chat_answer = None
    assert c.post("/api/chat", json={"message": "What remains?"}).status_code == 200
    prompt = fake.prompts[-1]
    convo = prompt.split("RECENT CONVERSATION:", 1)[1].split("USER QUESTION:", 1)[0]
    assert "Unrelated small talk question" in convo
    assert "Do AI tutors raise test scores?" not in convo
    assert hist[1]["content"] not in convo
    for s in removed:
        assert s["id"] not in prompt
        assert s["text"] not in prompt
    assert f"[[paper:{pid}]]" not in prompt
    # The new answer cites only evidence that still exists.
    bot = c.get("/api/chat/messages").json()["messages"][-1]
    assert bot["cited"] and all(q["paper_title"] != PAPERS[pid][0] for q in bot["cited"])


def test_removed_evidence_records_are_not_duplicated(env):
    c, _ = env
    cited_paper(c)
    with db.connect() as conn:
        ids = [m["id"] for m in db.chat_messages(conn, TEST_SESSION)]
        paper_a = {"id": "paper-a", "title": "A"}
        paper_b = {"id": "paper-b", "title": "B"}
        db.flag_chat_messages(conn, ids, paper_a)
        db.flag_chat_messages(conn, ids, paper_a)   # the same paper again: no duplicate entry
        db.flag_chat_messages(conn, ids, paper_b)   # another paper: appended
        rows = db.chat_messages(conn, TEST_SESSION)
    for m in rows:
        assert m["evidence_removed"] is True
        assert m["removed_evidence"] == [{"paper_id": "paper-a", "title": "A"}, {"paper_id": "paper-b", "title": "B"}]


def test_turn_asked_about_a_removed_paper_is_flagged_as_a_whole():
    rows = [{"id": 1, "role": "user", "content": "About this", "context": {"kind": "paper", "id": "x"}},
            {"id": 2, "role": "assistant", "content": "Answer", "cited_sentence_ids": ["y:p1_s1"]},
            {"id": 3, "role": "user", "content": "Other"},
            {"id": 4, "role": "assistant", "content": "See [[claim:7]]"},
            {"id": 5, "role": "user", "content": "Again", "context": {"kind": "relationship", "id": 9}},
            {"id": 6, "role": "assistant", "content": "Fine"},
            {"id": 7, "role": "user", "content": "Last"},
            {"id": 8, "role": "assistant", "content": "Nothing", "cited_sentence_ids": ["z:p1_s1"]}]
    fp = {"sentences": ["x:p1_s1"], "claims": [7], "relationships": [9]}
    assert pipeline.turns_touching(rows, fp, "x") == [1, 2, 3, 4, 5, 6]


# ---------- concurrency: in-flight work never restores deleted content ----------
def test_paper_clears_are_refused_while_an_upload_is_being_analyzed(env, monkeypatch):
    c, _ = env
    seen = {}
    real_compare = pipeline.compare_claims

    def mid_upload(llm, claim_ids, question=None, refresh=False):
        seen["all"] = c.delete("/api/papers", params={"confirm": "true"})
        seen["one"] = c.delete("/api/papers/paper-a", params={"confirm": "true"})
        seen["chat"] = c.delete("/api/chat/messages", params={"confirm": "true"})
        return real_compare(llm, claim_ids, question, refresh)

    monkeypatch.setattr(pipeline, "compare_claims", mid_upload)
    r = c.post("/api/papers", files={"file": ("e.pdf", pdf(PAPERS["paper-c"][1] + "\nA new extra sentence."),
                                              "application/pdf")}, data={"mode": "library", "title": "Extra"})
    assert r.status_code == 200, r.text
    for k in ("all", "one"):
        assert seen[k].status_code == 409 and seen[k].json()["detail"]["code"] == "analysis_running"
    assert seen["chat"].status_code == 200  # the chat is independent of an upload
    assert counts()["papers"] == len(PAPERS) + 1


def test_upload_that_has_not_stored_its_paper_yet_is_discarded_after_a_clear(env, monkeypatch):
    """Window between the upload's checks and storing the paper (no 'processing' row yet)."""
    c, _ = env
    real_check = pipeline._check_upload

    def check_then_clear(*a, **kw):
        q = real_check(*a, **kw)
        assert c.delete("/api/papers", params={"confirm": "true"}).status_code == 200  # e.g. from another tab
        return q

    monkeypatch.setattr(pipeline, "_check_upload", check_then_clear)
    r = c.post("/api/papers", files={"file": ("e.pdf", pdf(PAPER_D[1]), "application/pdf")},
               data={"mode": "library", "title": "Late"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "data_changed"
    assert counts()["papers"] == 0 and counts()["sentences"] == 0


def test_late_chat_answer_after_clear_chat_is_not_saved(env, monkeypatch):
    c, fake = env
    c.post("/api/chat", json={"message": "First"})
    real = fake.generate_json

    def clear_during_answer(prompt, model_cls, temperature=None):
        out = real(prompt, model_cls, temperature)
        if model_cls is ChatAnswer:
            assert c.delete("/api/chat/messages", params={"confirm": "true"}).status_code == 200
        return out

    monkeypatch.setattr(fake, "generate_json", clear_during_answer)
    r = c.post("/api/chat", json={"message": "Second"})
    assert r.status_code == 409 and r.json()["detail"] == {"message": db.StaleOperation("chat").args[0],
                                                           "code": "data_changed", "cleared": "chat"}
    assert c.get("/api/chat/messages").json()["messages"] == []


def test_late_chat_answer_after_a_paper_is_removed_is_not_saved(env, monkeypatch):
    c, fake = env
    real = fake.generate_json

    def remove_during_answer(prompt, model_cls, temperature=None):
        out = real(prompt, model_cls, temperature)
        if model_cls is ChatAnswer:
            for pid in PAPERS:  # whichever paper the answer cites is gone before it is stored
                assert c.delete(f"/api/papers/{pid}", params={"confirm": "true"}).status_code == 200
        return out

    monkeypatch.setattr(fake, "generate_json", remove_during_answer)
    r = c.post("/api/chat", json={"message": "Do tutors help?"})
    assert r.status_code == 409 and r.json()["detail"]["cleared"] == "papers"
    assert counts()["chat_messages"] == 0


def test_reanalysis_after_a_clear_writes_nothing(env, monkeypatch):
    c, fake = env
    assert c.put("/api/workspace", json={"question": "Do AI tutors help low performers?",
                                         "keep_papers": True}).status_code == 200
    real_extract = pipeline.extract_claims

    def clear_mid_run(llm, paper_id, question=None):
        # Simulates a clear landing between the 'processing' mark and the write of new claims.
        with db.connect() as conn:
            conn.execute("UPDATE papers SET analysis_status = 'pending'")
        assert c.delete("/api/papers", params={"confirm": "true"}).status_code == 200
        return real_extract(llm, paper_id, question)

    monkeypatch.setattr(pipeline, "extract_claims", clear_mid_run)
    r = c.post("/api/papers/paper-a/analyze")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "data_changed"
    assert all(v == 0 for v in counts().values())


def test_question_reset_discards_in_flight_answers(env, monkeypatch):
    c, fake = env
    real = fake.generate_json

    def reset_during_answer(prompt, model_cls, temperature=None):
        out = real(prompt, model_cls, temperature)
        if model_cls is ChatAnswer:
            assert c.put("/api/workspace", json={"question": "A different question?", "reset": True}).status_code == 200
        return out

    monkeypatch.setattr(fake, "generate_json", reset_during_answer)
    assert c.post("/api/chat", json={"message": "Do tutors help?"}).status_code == 409
    assert counts()["chat_messages"] == 0


def test_a_clear_waits_for_an_in_flight_write_and_then_removes_it(env):
    """Row-lock ordering across connections (another tab / process): a guarded write that already holds the
    epoch row commits first; the clear then removes what it wrote. A write starting after the clear aborts."""
    done = threading.Event()

    def other_tab():
        with db.use_session(TEST_SESSION):
            pipeline.clear_chat()
        done.set()

    with db.guarded(papers=True, chat=True):
        with db.connect() as conn:
            db.insert_chat_message(conn, {"session_id": TEST_SESSION, "role": "user", "content": "in flight",
                                          "question": "q"})
            t = threading.Thread(target=other_tab)
            t.start()
            time.sleep(0.4)
            assert not done.is_set()  # the clear is blocked by our open transaction
        t.join(5)
        assert done.is_set()
        with pytest.raises(db.StaleOperation):
            with db.connect() as conn:
                db.insert_chat_message(conn, {"session_id": TEST_SESSION, "role": "user", "content": "too late",
                                              "question": "q"})
    assert counts()["chat_messages"] == 0


def test_existing_projects_gain_the_new_tables_on_first_use(env):
    """Projects created before this feature (no data_epochs / flag columns) are upgraded additively."""
    with db.connect() as conn:
        conn.execute("DROP TABLE data_epochs")
        conn.execute("ALTER TABLE chat_messages DROP COLUMN evidence_removed, DROP COLUMN removed_evidence")
    db._upgraded.discard(db.schema_for(TEST_SESSION))
    papers_before = counts()["papers"]
    c, _ = env
    assert c.post("/api/chat", json={"message": "Do tutors help?"}).status_code == 200
    assert counts()["papers"] == papers_before
    assert c.delete("/api/chat/messages", params={"confirm": "true"}).json()["deleted"] == 2
