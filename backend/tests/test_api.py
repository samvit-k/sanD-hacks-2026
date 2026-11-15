import fitz
import httpx
import pytest
from fastapi.testclient import TestClient

from app import discover as discovery
from app import llm as llm_mod
from app import main, pipeline
from app.config import settings

from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, pdf, seed


@pytest.fixture
def client(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    monkeypatch.setattr(settings, "brightdata_token", "")
    pipeline._question_vecs.clear()
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield TestClient(main.app), fake
    llm_mod.set_llm(None)


def test_upload_flow_and_read_endpoints(client):
    c, fake = client
    seed(fake)
    r = c.post("/api/papers", files={"file": ("d.pdf", pdf(PAPER_D[1]), "application/pdf")},
               data={"title": PAPER_D[0]})
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["impacts"] and summary["timings_s"]["impact"] >= 0

    g = c.get("/api/graph").json()
    rel_id = next(iter(g["relationships"]))
    rel = c.get(f"/api/relationships/{rel_id}").json()
    assert rel["claim_a"]["quotes"] and rel["claim_b"]["quotes"]
    claim_id = next(iter(g["claims"]))
    assert c.get(f"/api/claims/{claim_id}").json()["quotes"]
    assert c.get("/api/claims/999999").status_code == 404
    assert c.get("/api/impact/latest").json()["paper"]["id"] == summary["paper"]["id"]
    assert c.get("/api/checks").json()["ok"] is True


def test_discover_defaults_to_latest_new_paper_and_shows_in_graph(client, monkeypatch):
    c, fake = client
    seed(fake)
    paper_id = c.post("/api/papers", files={"file": ("d.pdf", pdf(PAPER_D[1]), "application/pdf")},
                      data={"title": PAPER_D[0]}).json()["paper"]["id"]
    oa = {"results": [{"id": "https://openalex.org/W9", "title": "Related tutoring study", "publication_year": 2022}]}
    mock = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=oa)))
    real = discovery.discover
    monkeypatch.setattr(discovery, "discover", lambda q, for_paper_id=None: real(q, for_paper_id, client=mock))
    r = c.post("/api/discover", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == PAPER_D[0] and body["for_paper_id"] == paper_id and body["source"] == "openalex"
    g = c.get("/api/graph").json()
    disc = [n["data"] for n in g["nodes"] if n["data"]["type"] == "discovery"]
    assert disc and disc[0]["anchor"] == f"paper:{paper_id}"
    assert c.post("/api/discover", json={"paper_id": "missing"}).status_code == 404


def test_blank_pdf_returns_clear_422(client):
    c, _ = client
    doc = fitz.open()
    doc.new_page()
    r = c.post("/api/papers", files={"file": ("scan.pdf", doc.tobytes(), "application/pdf")})
    assert r.status_code == 422 and "No extractable text" in r.json()["detail"]


def test_health_reports_without_secrets(client):
    c, _ = client
    body = c.get("/api/health").json()
    assert set(body) >= {"database", "gemini", "brightdata"}
    assert settings.database_url not in str(body)
