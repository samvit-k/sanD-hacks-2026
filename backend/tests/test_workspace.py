"""User-defined research question: workspace API, library vs evaluate uploads, question changes, resets."""
import json

import fitz
import pytest
from fastapi.testclient import TestClient

import seed as seed_mod
from app import db, pipeline
from app import main
from app.config import settings

from .conftest import DEFAULT_TEST_QUESTION, clear_question
from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, PAPERS, pdf
from .test_seed import manifest  # noqa: F401  (fixture)
from .conftest import SESSION_HEADERS  # noqa: E402

USER_Q = "Does spaced repetition improve vocabulary retention?"
EXTRACT, PAIR, IMPACT = "You are extracting testable", "You are verifying the relationship", "added a NEW paper"
QUIET = lambda *_: None  # noqa: E731

@pytest.fixture
def api(fake_llm):
    yield TestClient(main.app, headers=SESSION_HEADERS), fake_llm

def upload(c, text, title, mode=None):
    data = {"title": title, **({"mode": mode} if mode else {})}
    return c.post("/api/papers", files={"file": ("p.pdf", pdf(text), "application/pdf")}, data=data)

def build_library(c):
    out = [upload(c, text, title, mode="library") for title, text in PAPERS.values()]
    assert all(r.status_code == 200 for r in out), [r.text for r in out]
    return [r.json() for r in out]

def counts():
    with db.connect() as conn:
        return db.analysis_counts(conn)

# ---------- GET / PUT /api/workspace ----------
def test_workspace_reports_stored_then_user_question(api):
    c, _ = api
    w = c.get("/api/workspace").json()
    assert w["question"] == DEFAULT_TEST_QUESTION and w["source"] == "workspace" and not w["is_default"]
    assert w["has_library"] is False and w["counts"]["papers"] == 0
    r = c.put("/api/workspace", json={"question": f"  {USER_Q}  "})
    assert r.status_code == 200 and r.json()["question"] == USER_Q and r.json()["reset"] is False
    w = c.get("/api/workspace").json()
    assert w["source"] == "workspace" and not w["is_default"]
    assert c.get("/api/health").json()["question"] == USER_Q

def test_no_question_anywhere(api, monkeypatch):
    c, _ = api
    clear_question()  # the .env default is never applied to a session implicitly (Stage D)
    w = c.get("/api/workspace").json()
    assert w["question"] is None and w["source"] is None
    g = c.get("/api/graph").json()
    assert g["question"] is None and g["nodes"][0]["data"]["label"] == "No research question set"
    assert g["edges"] == [] and g["claims"] == {}
    for mode in ("library", "evaluate"):
        r = upload(c, PAPERS["paper-a"][1], "A", mode=mode)
        assert r.status_code == 409 and "Set a research question first" in r.json()["detail"]
    assert counts()["papers"] == 0

def test_question_validation(api):
    c, _ = api
    assert c.put("/api/workspace", json={"question": "   "}).status_code == 422
    assert c.put("/api/workspace", json={"question": "x" * 301}).status_code == 422
    assert c.put("/api/workspace", json={"question": "x" * 300}).status_code == 200

def test_empty_graph_with_question(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    g = c.get("/api/graph").json()
    assert g["question"] == USER_Q and [n["data"]["type"] for n in g["nodes"]] == ["question"]
    assert g["relationships"] == {} and g["discovery"] is None

# ---------- library and evaluate uploads ----------
def test_library_uploads_build_graph_without_impacts(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    results = build_library(c)
    assert [r["mode"] for r in results] == ["library"] * 3
    assert all(r["claims_kept"] >= 1 and r["paper"]["is_new"] is False for r in results)
    assert results[0]["relationships_created"] == 0  # nothing to compare against yet
    assert sum(r["relationships_created"] for r in results[1:]) > 0
    assert results[-1]["library_papers"] == 3
    n = counts()
    assert n["library_papers"] == 3 and n["evaluated_papers"] == 0 and n["impacts"] == 0
    g = c.get("/api/graph").json()
    assert g["question"] == USER_Q and len(g["relationships"]) == n["relationships"]
    assert c.get("/api/checks").json()["ok"] is True
    assert c.get("/api/workspace").json()["has_library"] is True

def test_evaluate_without_library_rejected_and_nothing_stored(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    r = upload(c, PAPER_D[1], PAPER_D[0])  # default mode = evaluate
    assert r.status_code == 409 and "no evidence library" in r.json()["detail"]
    assert counts()["papers"] == 0

def test_evaluate_after_library_produces_impacts(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    r = upload(c, PAPER_D[1], PAPER_D[0], mode="evaluate")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["mode"] == "evaluate" and s["question"] == USER_Q and s["paper"]["is_new"]
    assert s["impacts"] and all(i["cited"] for i in s["impacts"])
    assert counts()["impacts"] == len(s["impacts"])
    assert c.get("/api/impact/latest").json()["paper"]["id"] == s["paper"]["id"]

def test_default_mode_is_still_evaluate(api):
    c, _ = api
    build_library(c)
    s = upload(c, PAPER_D[1], PAPER_D[0]).json()
    assert s["mode"] == "evaluate" and "impacts" in s

def test_invalid_mode(api):
    c, _ = api
    assert upload(c, PAPER_D[1], "x", mode="other").status_code == 422

def test_user_question_reaches_every_gemini_prompt(api):
    c, fake = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    upload(c, PAPER_D[1], PAPER_D[0], mode="evaluate")
    kinds = {k: [p for p in fake.prompts if k in p] for k in (EXTRACT, PAIR, IMPACT)}
    assert all(kinds.values()), {k: len(v) for k, v in kinds.items()}
    for prompts in kinds.values():
        assert all(USER_Q in p for p in prompts)
        assert not any(DEFAULT_TEST_QUESTION in p for p in prompts)

# ---------- changing the question ----------
def test_changing_question_requires_reset(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    upload(c, PAPER_D[1], PAPER_D[0])
    before = counts()
    with db.connect() as conn:
        db.cache_put(conn, "k", "q", "openalex", {"results": []})

    r = c.put("/api/workspace", json={"question": "Do standing desks improve focus?"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["current_question"] == USER_Q and detail["counts"]["papers"] == before["papers"]
    assert counts() == before and c.get("/api/workspace").json()["question"] == USER_Q

    r = c.put("/api/workspace", json={"question": "Do standing desks improve focus?", "reset": True})
    assert r.status_code == 200 and r.json()["reset"] is True
    assert r.json()["question"] == "Do standing desks improve focus?"
    assert all(v == 0 for v in counts().values())
    with db.connect() as conn:
        assert db.cache_get(conn, "k") is not None  # discovery cache is question-independent; kept
        assert conn.execute("SELECT count(*) AS n FROM discovery_links").fetchone()["n"] == 0

def test_same_question_never_deletes_even_with_reset(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    before = counts()
    for body in ({"question": USER_Q}, {"question": f" {USER_Q} ", "reset": True}):
        r = c.put("/api/workspace", json=body)
        assert r.status_code == 200 and r.json()["reset"] is False
        assert counts() == before

def test_confirming_default_question_keeps_analysis(api):
    c, _ = api
    build_library(c)  # analysed under the .env default
    before = counts()
    r = c.put("/api/workspace", json={"question": DEFAULT_TEST_QUESTION})
    assert r.status_code == 200 and not r.json()["reset"] and counts() == before
    assert c.get("/api/workspace").json()["source"] == "workspace"

def test_reset_is_all_or_nothing(api, monkeypatch):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    before = counts()

    def boom(conn, q):
        raise RuntimeError("simulated failure after the delete")
    monkeypatch.setattr(db, "set_question", boom)
    with pytest.raises(RuntimeError):
        pipeline.set_workspace_question("A different question?", reset=True)
    assert counts() == before  # the delete was rolled back
    with db.connect() as conn:
        assert db.get_question(conn) == USER_Q

def test_schema_reinit_keeps_existing_data(api):
    c, _ = api
    c.put("/api/workspace", json={"question": USER_Q})
    build_library(c)
    before = counts()
    db.init_schema()
    db.init_schema()
    assert counts() == before and c.get("/api/workspace").json()["question"] == USER_Q

def test_blank_pdf_in_library_mode(api):
    c, _ = api
    doc = fitz.open()
    doc.new_page()
    r = c.post("/api/papers", files={"file": ("s.pdf", doc.tobytes(), "application/pdf")}, data={"mode": "library"})
    assert r.status_code == 422 and counts()["papers"] == 0

# ---------- seed.py ----------
def test_seed_question_protection(clean_db, manifest):  # noqa: F811
    clear_question()
    with db.connect() as conn:
        assert db.get_question(conn) is None and db.analysis_counts(conn)["papers"] == 0  # isolated start
    llm = FakeLLM()
    out = []
    seed_mod.seed(llm, manifest, question=USER_Q, out=out.append)
    assert f"research question: {USER_Q}" in out
    assert all(USER_Q in p for p in llm.prompts if EXTRACT in p or PAIR in p)
    before = counts()
    with pytest.raises(SystemExit, match="different question"):
        seed_mod.seed(FakeLLM(), manifest, question="Another question?", out=QUIET)
    assert counts() == before
    seed_mod.seed(FakeLLM(), manifest, question="Another question?", reset=True, out=QUIET)
    with db.connect() as conn:
        assert db.get_question(conn) == "Another question?"
    assert counts()["library_papers"] == 3

def test_seed_without_any_question_fails_clearly(clean_db, manifest, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "research_question", None)  # no .env default
    clear_question()
    with db.connect() as conn:
        assert db.get_question(conn) is None  # no workspace question
    assert "question" not in json.loads(manifest.read_text())  # no manifest question
    with pytest.raises(SystemExit, match="No research question"):
        seed_mod.seed(FakeLLM(), manifest, out=QUIET)  # and no --question
    assert counts()["papers"] == 0