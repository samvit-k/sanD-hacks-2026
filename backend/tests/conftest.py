import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app import llm as llm_mod, pipeline  # noqa: E402
from app.config import settings  # noqa: E402

TEST_SESSION = "0123456789ab"  # the session every database test runs in (isolated test database)
SESSION_HEADERS = {"X-Session-Id": TEST_SESSION}
DEFAULT_TEST_QUESTION = "Do AI tutors improve learning?"

@pytest.fixture(scope="session")
def pg_server():
    """Embedded PostgreSQL + pgvector (pgserver) — stands in for Tiger Data in tests."""
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="rl-pg-"), cleanup_mode="stop")
    yield srv

@pytest.fixture(scope="session")
def pg_url(pg_server):
    url = pg_server.get_uri()
    old = settings.database_url
    settings.database_url = url
    db.init_schema()
    db.create_session("Test session", session_id=TEST_SESSION)
    yield url
    settings.database_url = old

@pytest.fixture(autouse=True)
def session_scope(request):
    """Database tests run inside the test session (explicitly bound; there is no default session)."""
    if "pg_url" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("pg_url")
    with db.use_session(TEST_SESSION):
        yield

@pytest.fixture(autouse=True)
def default_question(monkeypatch):
    """The seed CLI's optional .env default question."""
    monkeypatch.setattr(settings, "research_question", DEFAULT_TEST_QUESTION)

def drop_other_sessions():
    for row in db.list_sessions():
        if row["id"] != TEST_SESSION:
            db.delete_session(row["id"])

@pytest.fixture
def clean_db(pg_url):
    """Empty test session holding DEFAULT_TEST_QUESTION (as the pre-session tests assumed); other sessions removed."""
    drop_other_sessions()
    with db.connect() as conn:
        db.delete_all(conn)
        conn.execute("TRUNCATE public.discovery_cache CASCADE")
        conn.execute("TRUNCATE workspace")
        db.set_question(conn, DEFAULT_TEST_QUESTION)
    yield pg_url

@pytest.fixture
def fake_llm(clean_db, monkeypatch):
    """Empty test session with the deterministic FakeLLM installed (never Gemini) and a matching threshold low enough
    for its toy embeddings. Yields the FakeLLM; removed again afterwards."""
    from .fake_llm import FakeLLM
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    pipeline._question_vecs.clear()
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)

def clear_question():
    """Remove the test session's question (a session with no confirmed question)."""
    with db.connect() as conn:
        conn.execute("TRUNCATE workspace")