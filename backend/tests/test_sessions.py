"""Stage D: research sessions — lifecycle, titles, persistence, and complete isolation at the database and API
layers. Runs on the isolated test database only."""
import contextvars
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline
from app import llm as llm_mod
from app.config import settings

from .conftest import DEFAULT_TEST_QUESTION, TEST_SESSION
from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, PAPERS, pdf

BACKEND = Path(__file__).resolve().parents[1]
Q_B = "Does spaced repetition improve vocabulary retention?"
B_PAPER = ("Spaced repetition study", "Abstract\nA vocabulary study with adult learners.\nResults\n"
           "Spaced repetition improved vocabulary retention by 18 percent after 6 weeks.\n"
           "Massed practice improved retention by 7 percent after 6 weeks.\nDiscussion\nSpacing helped.")


@pytest.fixture
def env(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    pipeline._question_vecs.clear()
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)


def client(sid=None):
    return TestClient(main.app, headers={"X-Session-Id": sid} if sid else {})


def new_session(question=None, title=None):
    r = client().post("/api/sessions", json={"title": title})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    if question:
        assert client(sid).put("/api/workspace", json={"question": question}).status_code == 200
    return sid


def upload(sid, title, text, mode="library", data=None):
    return client(sid).post("/api/papers", files={"file": ("p.pdf", data or pdf(text), "application/pdf")},
                            data={"title": title, "mode": mode})


def build_a():
    for title, text in PAPERS.values():
        assert upload(TEST_SESSION, title, text).status_code == 200


def fingerprint(sid):
    """Everything a session owns, read through its own scope."""
    with db.use_session(sid), db.connect() as conn:
        return {
            "question": db.get_question(conn),
            "papers": sorted(p["id"] for p in db.all_papers(conn)),
            "sentences": conn.execute("SELECT count(*) AS n FROM sentences").fetchone()["n"],
            "claims": sorted((c["id"], c["text"]) for c in db.all_claims(conn)),
            "relationships": sorted((r["id"], r["label"]) for r in db.all_relationships(conn)),
            "impacts": conn.execute("SELECT count(*) AS n FROM impacts").fetchone()["n"],
            "refs": db.node_ref_map(conn),
            "chat": [m["content"] for m in db.chat_messages(conn, sid)],
        }


# ---------- lifecycle and titles ----------
def test_create_list_get_rename_and_auto_titles(env):
    sid = new_session()
    s = client().get(f"/api/sessions/{sid}").json()
    assert s["title"] == db.AUTO_TITLE and s["title_source"] == "auto" and s["question"] is None
    assert s["created_at"] and s["updated_at"]
    client(sid).put("/api/workspace", json={"question": Q_B})
    assert client().get(f"/api/sessions/{sid}").json()["title"] == db.auto_title(Q_B)  # follows the question
    r = client().patch(f"/api/sessions/{sid}", json={"title": "  Vocabulary   project "})
    assert r.json()["title"] == "Vocabulary project" and r.json()["title_source"] == "manual"
    client(sid).put("/api/workspace", json={"question": "Does interleaving help vocabulary?"})
    assert client().get(f"/api/sessions/{sid}").json()["title"] == "Vocabulary project"  # never auto-renamed again
    r = client().patch(f"/api/sessions/{sid}", json={"title": None})  # explicit revert to automatic
    assert r.json()["title"] == "Does interleaving help vocabulary" and r.json()["title_source"] == "auto"
    assert client().patch(f"/api/sessions/{sid}", json={"title": "   "}).status_code == 422
    assert client().patch(f"/api/sessions/{sid}", json={"title": "x" * 121}).status_code == 422
    ids = [x["id"] for x in client().get("/api/sessions").json()["sessions"]]
    assert ids[0] == sid and TEST_SESSION in ids  # most recently updated first


def test_auto_title_shortens_long_questions():
    q = "How does AI-assisted tutoring influence student learning outcomes compared with traditional instruction?"
    t = db.auto_title(q)
    assert len(t) <= 60 and t.endswith("…") and not t.endswith(" …") and q.startswith(t[:-1])
    assert db.auto_title(None) == db.AUTO_TITLE


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sessions_survive_a_real_backend_restart(env, pg_url):
    """Data written by this process is read back by a SEPARATE, freshly started ResearchLens server process
    (uvicorn app.main:app) against the same isolated test database: nothing depends on process memory."""
    build_a()
    assert upload(TEST_SESSION, PAPER_D[0], PAPER_D[1], mode="evaluate").status_code == 200
    sid_b = new_session(Q_B, title="Vocabulary project")
    assert upload(sid_b, *B_PAPER).status_code == 200
    graphs = {sid: client(sid).get("/api/graph").json() for sid in (TEST_SESSION, sid_b)}  # assigns Node numbers
    before = {sid: (fingerprint(sid), graphs[sid]) for sid in (TEST_SESSION, sid_b)}
    a_refs = before[TEST_SESSION][0]["refs"]
    assert before[TEST_SESSION][0]["relationships"] and a_refs

    port = _free_port()
    proc_env = {**os.environ, "DATABASE_URL": pg_url, "GEMINI_API_KEY": "", "BRIGHTDATA_API_KEY": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level",
                             "warning"], cwd=BACKEND, env=proc_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            pytest.fail("restarted backend did not come up: " + proc.stdout.read1().decode())
        listed = {s["id"]: s for s in httpx.get(f"{base}/api/sessions").json()["sessions"]}
        assert listed[sid_b]["title"] == "Vocabulary project" and listed[sid_b]["title_source"] == "manual"
        assert listed[TEST_SESSION]["question"] == DEFAULT_TEST_QUESTION and listed[sid_b]["question"] == Q_B
        for sid in (TEST_SESSION, sid_b):
            h = {"X-Session-Id": sid}
            graph = httpx.get(f"{base}/api/graph", headers=h).json()
            graph.pop("session_id", None)
            expected = dict(before[sid][1])
            expected.pop("session_id", None)
            assert graph == expected  # same papers, claims, relationships, quotes and Node numbers
            assert httpx.get(f"{base}/api/checks", headers=h).json()["ok"]
        assert fingerprint(TEST_SESSION) == before[TEST_SESSION][0] and fingerprint(sid_b) == before[sid_b][0]
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---------- the session id is mandatory and verified ----------
def test_missing_invalid_and_unknown_session_ids_never_fall_back(env):
    for path in ("/api/workspace", "/api/graph", "/api/checks", "/api/chat/messages"):
        r = client().get(path)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "session_required", path
        assert client("../etc").get(path).json()["detail"]["code"] == "invalid_session"
        assert client("ffffffffffff").get(path).status_code == 404
    assert client().put("/api/workspace", json={"question": "Q?"}).status_code == 400
    assert client("ffffffffffff").post("/api/chat", json={"message": "hi"}).status_code == 404
    assert client().get("/api/health").json()["question"] is None  # no session -> no question leaks


def test_database_access_without_a_session_is_impossible():
    def attempt():
        with db.connect():
            pass
    with pytest.raises(db.NoSessionError):
        contextvars.Context().run(attempt)


# ---------- isolation ----------
def test_same_pdf_in_two_sessions_is_independent(env):
    sid_b = new_session(Q_B)
    title, text = PAPERS["paper-a"]
    same = pdf(text)  # the very same bytes uploaded to both sessions
    ra, rb = upload(TEST_SESSION, title, text, data=same), upload(sid_b, title, text, data=same)
    assert ra.status_code == rb.status_code == 200, (ra.text, rb.text)  # not a "duplicate" across sessions
    assert ra.json()["paper"]["id"] == rb.json()["paper"]["id"]  # same content-derived id, separate rows
    assert upload(sid_b, title, text, data=same).json()["detail"]["code"] == "duplicate"  # still one WITHIN B
    with db.use_session(sid_b), db.connect() as conn:
        db.delete_paper(conn, rb.json()["paper"]["id"])
    a = fingerprint(TEST_SESSION)
    assert a["papers"] == [ra.json()["paper"]["id"]] and a["claims"]  # A keeps its copy and claims
    assert fingerprint(sid_b)["papers"] == []


def test_graphs_claims_refs_checks_and_chat_are_per_session(env):
    build_a()
    assert upload(TEST_SESSION, PAPER_D[0], PAPER_D[1], mode="evaluate").status_code == 200
    sid_b = new_session(Q_B)
    assert upload(sid_b, *B_PAPER).status_code == 200
    ga, gb = client(TEST_SESSION).get("/api/graph").json(), client(sid_b).get("/api/graph").json()
    assert ga["question"] == DEFAULT_TEST_QUESTION and gb["question"] == Q_B
    assert set(ga["papers"]).isdisjoint(gb["papers"])
    texts_a = {c["text"] for c in ga["claims"].values()}
    assert not texts_a & {c["text"] for c in gb["claims"].values()}
    # Node numbers are per session: both start at Node 1, independently.
    refs_b = sorted(n["data"]["ref"] for n in gb["nodes"] if n["data"].get("ref"))
    assert refs_b[0] == 1 and min(n["data"]["ref"] for n in ga["nodes"] if n["data"].get("ref")) == 1
    # An id that exists only in A is not reachable through B.
    only_a = max(int(k) for k in ga["claims"])
    assert only_a not in {int(k) for k in gb["claims"]}
    assert client(sid_b).get(f"/api/claims/{only_a}").status_code == 404
    assert client(sid_b).get("/api/impact/latest").status_code == 404  # A's evaluation is not B's
    assert client(TEST_SESSION).get("/api/checks").json()["ok"] and client(sid_b).get("/api/checks").json()["ok"]
    assert client(sid_b).get("/api/checks").json()["session_id"] == sid_b


def test_chat_memory_and_vector_retrieval_never_cross_sessions(env):
    build_a()
    sid_b = new_session(Q_B)
    upload(sid_b, *B_PAPER)
    ra = client(TEST_SESSION).post("/api/chat", json={"message": "Where do the papers disagree?"})
    assert ra.status_code == 200
    rb = client(sid_b).post("/api/chat", json={"message": "What does the evidence say about retention?"})
    assert rb.status_code == 200, rb.text
    prompt_b = [p for p in env.prompts if "evidence say about retention" in p][-1]
    with db.use_session(TEST_SESSION), db.connect() as conn:
        a_sentences = [s["id"] for p in db.all_papers(conn) for s in db.paper_sentences(conn, p["id"])]
    assert a_sentences and not any(sid in prompt_b for sid in a_sentences)  # pgvector search only saw B
    assert "Where do the papers disagree?" not in prompt_b  # no conversation memory from A
    a_papers = {s.split(":")[0] for s in a_sentences}
    assert all(c["paper_id"] not in a_papers for c in rb.json()["assistant"]["cited"])
    msgs_a = client(TEST_SESSION).get("/api/chat/messages").json()["messages"]
    msgs_b = client(sid_b).get("/api/chat/messages").json()["messages"]
    assert [m["content"] for m in msgs_a][0] == "Where do the papers disagree?"
    assert [m["content"] for m in msgs_b][0] == "What does the evidence say about retention?" and len(msgs_b) == 2


def test_chat_context_cannot_point_into_another_session(env):
    build_a()
    sid_b = new_session(Q_B)
    upload(sid_b, *B_PAPER)
    a_ids = [int(k) for k in client(TEST_SESSION).get("/api/graph").json()["claims"]]
    b_ids = {int(k) for k in client(sid_b).get("/api/graph").json()["claims"]}
    foreign = max(a_ids)
    assert foreign not in b_ids
    r = client(sid_b).post("/api/chat", json={"message": "Explain this", "context": {"kind": "claim", "id": foreign}})
    assert r.status_code == 422  # resolved only against B's own claims


def test_pending_reanalysis_is_per_session(env):
    build_a()
    sid_b = new_session(Q_B)
    client(TEST_SESSION).put("/api/workspace", json={"question": "A new question for A?", "keep_papers": True})
    assert client(TEST_SESSION).get("/api/workspace").json()["counts"]["pending_papers"] == 3
    wb = client(sid_b).get("/api/workspace").json()
    assert wb["pending_papers"] == [] and wb["question"] == Q_B
    assert upload(sid_b, *B_PAPER).status_code == 200  # B is not blocked by A's pending papers
    assert upload(TEST_SESSION, *B_PAPER).json()["detail"]["code"] == "pending_analysis"
    pid = client(TEST_SESSION).get("/api/workspace").json()["pending_papers"][0]["id"]
    assert client(sid_b).post(f"/api/papers/{pid}/analyze").status_code == 404  # A's paper is not in B


def test_deleting_a_session_needs_confirmation_and_spares_the_others(env):
    build_a()
    sid_b = new_session(Q_B)
    upload(sid_b, *B_PAPER)
    client(sid_b).post("/api/chat", json={"message": "Summarize the evidence"})
    keep = fingerprint(TEST_SESSION)
    r = client().delete(f"/api/sessions/{sid_b}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirmation_required"
    assert client().delete(f"/api/sessions/{sid_b}", params={"confirm": TEST_SESSION}).status_code == 409
    assert client(sid_b).get("/api/workspace").status_code == 200  # still there
    r = client().delete(f"/api/sessions/{sid_b}", params={"confirm": sid_b})
    assert r.status_code == 200 and r.json()["counts"]["papers"] == 1
    assert client(sid_b).get("/api/graph").status_code == 404
    assert sid_b not in [s["id"] for s in client().get("/api/sessions").json()["sessions"]]
    assert fingerprint(TEST_SESSION) == keep  # the other project is untouched
    assert client().delete(f"/api/sessions/{sid_b}", params={"confirm": sid_b}).status_code == 404


def test_concurrent_requests_stay_in_their_own_sessions(env):
    sid_b = new_session(Q_B)
    errors = []

    def work(sid, marker):
        try:
            for i in range(15):
                with db.use_session(sid), db.connect() as conn:
                    db.insert_chat_message(conn, {"session_id": sid, "role": "user", "content": f"{marker}-{i}"})
                    rows = db.chat_messages(conn, sid)
                    assert all(m["content"].startswith(marker) for m in rows)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(TEST_SESSION, "A")), threading.Thread(target=work, args=(sid_b, "B"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(fingerprint(TEST_SESSION)["chat"]) == len(fingerprint(sid_b)["chat"]) == 15
