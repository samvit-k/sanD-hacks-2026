"""Regression tests: no half-ingested papers after Gemini failures; no futile retries on long quota waits."""
import pytest
from fastapi.testclient import TestClient

from app import db, llm as llm_mod, main, pipeline
from app.config import settings
from app.contracts import ExtractionResult
from app.llm import GeminiLLM, QuotaExhaustedError, quota_retry_delay

from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, PAPERS, pdf

# Shape of the real 429 seen in the live smoke test (trimmed).
DAILY_QUOTA = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota ...', "
               "'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
               "'retryDelay': '25080s'}]}}")
PER_MINUTE = DAILY_QUOTA.replace("25080s", "7s")


class FailExtract(FakeLLM):
    def generate_json(self, prompt, model_cls):
        if model_cls is ExtractionResult:
            raise RuntimeError("simulated Gemini 503")
        return super().generate_json(prompt, model_cls)


class FailEmbed(FakeLLM):
    def embed(self, texts):
        raise RuntimeError("simulated embedding failure")


class FailQuota(FakeLLM):
    def generate_json(self, prompt, model_cls):
        raise QuotaExhaustedError("Gemini quota exhausted; the API says to retry in about 7.0 h.")


# ---------- retry policy ----------
def run_retry(fn, monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_mod.time, "sleep", sleeps.append)
    return GeminiLLM._retry(object(), fn, "test"), sleeps  # _retry does not use instance state


def test_quota_retry_delay_parsing():
    assert quota_retry_delay(RuntimeError(DAILY_QUOTA)) == 25080
    assert quota_retry_delay(RuntimeError(PER_MINUTE)) == 7
    assert quota_retry_delay(RuntimeError("503 UNAVAILABLE high demand")) is None
    assert quota_retry_delay(RuntimeError("429 RESOURCE_EXHAUSTED no delay given")) is None


def test_long_quota_delay_fails_fast_without_retrying(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        raise RuntimeError(DAILY_QUOTA)
    with pytest.raises(QuotaExhaustedError, match="7.0 h"):
        run_retry(fn, monkeypatch)
    assert len(calls) == 1


def test_short_quota_delay_is_honoured_then_succeeds(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(PER_MINUTE)
        return "ok"
    result, sleeps = run_retry(fn, monkeypatch)
    assert result == "ok" and sleeps == [7]


def test_transient_errors_still_retried(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        raise RuntimeError("503 UNAVAILABLE")
    sleeps = []
    monkeypatch.setattr(llm_mod.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="503"):
        GeminiLLM._retry(object(), fn, "test")
    assert len(calls) == 3 and sleeps == [2, 4]


# ---------- upload cleanup ----------
@pytest.fixture
def api(clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    pipeline._question_vecs.clear()
    llm_mod.set_llm(FakeLLM())
    yield TestClient(main.app)
    llm_mod.set_llm(None)


def upload(c, text, mode):
    return c.post("/api/papers", files={"file": ("p.pdf", pdf(text), "application/pdf")},
                  data={"mode": mode, "title": "T " + text[:20]})


def state():
    with db.connect() as conn:
        return db.analysis_counts(conn), db.latest_new_paper(conn)


@pytest.mark.parametrize("failing", [FailExtract, FailEmbed, FailQuota])
def test_library_upload_failure_leaves_no_partial_paper(api, failing):
    c = api
    assert upload(c, PAPERS["paper-a"][1], "library").status_code == 200
    before = state()
    llm_mod.set_llm(failing())
    r = upload(c, PAPERS["paper-b"][1], "library")
    assert r.status_code == 503 and "was not saved" in r.json()["detail"]
    assert state() == before  # failed paper removed; existing paper untouched
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sentences WHERE paper_id NOT IN "
                            "(SELECT id FROM papers)").fetchone()["n"] == 0


@pytest.mark.parametrize("failing", [FailExtract, FailEmbed])
def test_evaluate_failure_does_not_become_latest_paper(api, failing):
    c = api
    for _, text in PAPERS.values():
        assert upload(c, text, "library").status_code == 200
    first = upload(c, PAPER_D[1], "evaluate")
    assert first.status_code == 200
    before = state()
    llm_mod.set_llm(failing())
    r = upload(c, PAPER_D[1] + "\nA different follow-up study.", "evaluate")
    assert r.status_code == 503
    assert state() == before
    assert c.get("/api/impact/latest").json()["paper"]["id"] == first.json()["paper"]["id"]


def test_quota_error_message_reaches_client(api):
    c = api
    llm_mod.set_llm(FailQuota())
    r = upload(c, PAPERS["paper-a"][1], "library")
    assert r.status_code == 503 and "quota exhausted" in r.json()["detail"]
    assert state()[0]["papers"] == 0
