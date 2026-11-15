import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402


@pytest.fixture(scope="session")
def pg_url():
    """Embedded PostgreSQL + pgvector (pgserver) — stands in for Tiger Data in tests."""
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="rl-pg-"), cleanup_mode="stop")
    url = srv.get_uri()
    old = settings.database_url
    settings.database_url = url
    db.init_schema()
    yield url
    settings.database_url = old


DEFAULT_TEST_QUESTION = "Do AI tutors improve learning?"


@pytest.fixture(autouse=True)
def default_question(monkeypatch):
    """Existing tests rely on the optional .env default; workspace tests override or clear it."""
    monkeypatch.setattr(settings, "research_question", DEFAULT_TEST_QUESTION)


@pytest.fixture
def clean_db(pg_url):
    with db.connect() as conn:
        db.delete_all(conn)
        conn.execute("TRUNCATE discovery_cache CASCADE")
        conn.execute("TRUNCATE workspace")
    yield pg_url
