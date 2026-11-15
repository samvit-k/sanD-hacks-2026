"""Stage D migration: single-workspace (public) layout -> one research session. Every test runs on its OWN
throwaway database built with the exact Stage C schema (tests/fixtures/legacy_schema_stage_c.sql)."""
import secrets
from pathlib import Path

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app import db, main, migrate, pipeline
from app.config import settings

LEGACY_SQL = (Path(__file__).parent / "fixtures" / "legacy_schema_stage_c.sql").read_text()
QUESTION = "Do intelligent tutoring systems improve student learning outcomes?"


@pytest.fixture
def fresh_db(pg_server, monkeypatch):
    """An empty database on the test server; settings point at it for the duration of the test."""
    name = f"mig_{secrets.token_hex(4)}"
    with psycopg.connect(pg_server.get_uri(), autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    url = pg_server.get_uri(name)
    monkeypatch.setattr(settings, "database_url", url)
    yield url
    with psycopg.connect(pg_server.get_uri(), autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def vec(seed):
    v = np.random.default_rng(seed).random(768).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def legacy_db(fresh_db):
    """A populated Stage C database: every research table has rows (incl. embeddings, citations, node numbers)."""
    with psycopg.connect(fresh_db, autocommit=True) as c:
        c.execute(LEGACY_SQL)
    with psycopg.connect(fresh_db, row_factory=dict_row) as c:
        register_vector(c)
        c.execute("INSERT INTO workspace (id, question) VALUES (1, %s)", (QUESTION,))
        for pid, title, new in (("paper-a-aaaaaa", "Paper A", False), ("paper-b-bbbbbb", "Paper B", False),
                                ("paper-d-dddddd", "Paper D", True)):
            c.execute("INSERT INTO papers (id, title, is_new, content_sha256, relevance) VALUES (%s, %s, %s, %s, 0.7)",
                      (pid, title, new, secrets.token_hex(32)))
            for i in range(3):
                c.execute("INSERT INTO sentences (id, paper_id, page, idx, section, text) VALUES (%s, %s, 1, %s, 'results', %s)",
                          (f"{pid}:p1_s{i}", pid, i, f"{title} sentence {i}: scores rose {i + 5} points."))
        claims = [("paper-a-aaaaaa", 0), ("paper-a-aaaaaa", 1), ("paper-b-bbbbbb", 0), ("paper-d-dddddd", 0)]
        for n, (pid, i) in enumerate(claims):
            c.execute("INSERT INTO claims (paper_id, text, conditions, sentence_ids, embedding) VALUES "
                      "(%s, %s, '{\"duration\": \"12 weeks\"}', %s, %s)", (pid, f"claim {n}", [f"{pid}:p1_s{i}"], vec(n)))
        c.execute("INSERT INTO relationships (claim_a, claim_b, label, similarity, explanation, cited_sentence_ids) "
                  "VALUES (1, 3, 'supports', 0.81, 'Both report gains.', %s), (3, 4, 'apparent_contradiction', 0.7, "
                  "'Durations differ.', %s)", (["paper-a-aaaaaa:p1_s0", "paper-b-bbbbbb:p1_s0"],
                                                ["paper-b-bbbbbb:p1_s0", "paper-d-dddddd:p1_s0"]))
        c.execute("INSERT INTO impacts (new_claim_id, prior_claim_id, relationship_id, impact, rationale, cited_sentence_ids) "
                  "VALUES (4, 3, 2, 'qualifies', 'Shorter study.', %s)", (["paper-b-bbbbbb:p1_s0", "paper-d-dddddd:p1_s0"],))
        for node in ("paper:paper-a-aaaaaa", "claim:1", "claim:2", "paper:paper-b-bbbbbb", "claim:3",
                     "paper:paper-d-dddddd", "claim:4"):
            c.execute("INSERT INTO node_refs (node_id) VALUES (%s)", (node,))
        c.execute("INSERT INTO chat_messages (role, content, question, cited_sentence_ids) VALUES "
                  "('user', 'Do the papers agree?', %s, '{}'), ('assistant', 'According to [[claim:1]] ...', %s, %s)",
                  (QUESTION, QUESTION, ["paper-a-aaaaaa:p1_s0"]))
        c.execute("INSERT INTO discovery_cache (query_hash, query, source, results) VALUES ('h1', 'paper d', 'openalex', '{\"results\": []}')")
        c.execute("INSERT INTO discovery_links (paper_id, query_hash) VALUES ('paper-d-dddddd', 'h1')")
        c.commit()
    return fresh_db


def legacy_columns(url):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {t: migrate.table_columns(c, "public", t) for t in db.SESSION_TABLES}


def fingerprints(url, schema, columns=None):
    """With `columns` (captured before the move), only the original columns are compared; additive columns added
    by the per-session DDL (e.g. papers.extraction_report) are not a change to existing data."""
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {t: migrate.table_fingerprint(c, schema, t, (columns or {}).get(t)) for t in db.SESSION_TABLES}


def layout(url):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return migrate.inspect_layout(c)


def assert_untouched(url, before):
    assert layout(url)["state"] == "legacy"
    assert fingerprints(url, "public") == before
    with psycopg.connect(url, row_factory=dict_row) as c:
        assert c.execute("SELECT to_regclass('public.sessions') AS r").fetchone()["r"] is None
        assert not c.execute("SELECT 1 FROM pg_namespace WHERE nspname LIKE 'rl\\_%'").fetchone()


# ---------- 1-3, 10: a successful migration ----------
def test_migration_moves_every_table_without_changing_a_row(legacy_db):
    before = fingerprints(legacy_db, "public")
    cols = legacy_columns(legacy_db)
    assert all(f["rows"] > 0 for f in before.values())
    report = migrate.migrate_legacy()
    assert report["migrated"] and report["verified_identical"] and report["question"] == QUESTION
    sid, schema = report["session_id"], report["schema"]
    assert fingerprints(legacy_db, schema, cols) == before == report["tables"]
    assert layout(legacy_db) == {"state": "migrated", "problems": [], "legacy_tables": [], "has_registry": True,
                                 "sessions": [sid]}
    s = db.get_session(sid)
    assert s["title"] == db.auto_title(QUESTION) and s["title_source"] == "auto"
    with psycopg.connect(legacy_db, row_factory=dict_row) as c:  # the shared cache stays shared
        assert c.execute("SELECT count(*) AS n FROM public.discovery_cache").fetchone()["n"] == 1


def test_migrated_research_is_readable_through_the_session_interface(legacy_db):
    sid = migrate.migrate_legacy()["session_id"]
    with db.use_session(sid):
        assert pipeline.current_question() == QUESTION
        g = pipeline.graph_payload()
        assert set(g["papers"]) == {"paper-a-aaaaaa", "paper-b-bbbbbb", "paper-d-dddddd"}
        assert {n["data"]["id"]: n["data"].get("ref") for n in g["nodes"] if n["data"].get("ref")} == {
            "paper:paper-a-aaaaaa": 1, "claim:1": 2, "claim:2": 3, "paper:paper-b-bbbbbb": 4, "claim:3": 5,
            "paper:paper-d-dddddd": 6, "claim:4": 7}  # node numbers preserved
        rel = g["relationships"]["1"]
        assert [q["id"] for q in rel["cited"]["a"] + rel["cited"]["b"]] == ["paper-a-aaaaaa:p1_s0", "paper-b-bbbbbb:p1_s0"]
        assert rel["cited"]["a"][0]["text"] == "Paper A sentence 0: scores rose 5 points."  # verbatim, from its paper
        with db.connect() as conn:
            assert not any(db.integrity_violations(conn).values())
            assert db.nearest_claims(conn, vec(2), 1) == [3]  # embeddings intact and searchable
            assert [m["content"] for m in db.chat_messages(conn, sid)][0] == "Do the papers agree?"


def test_sequences_constraints_and_indexes_still_work_after_the_move(legacy_db):
    sid = migrate.migrate_legacy()["session_id"]
    schema = db.schema_for(sid)
    with psycopg.connect(legacy_db, row_factory=dict_row) as c:
        idx = {r["indexname"] for r in c.execute("SELECT indexname FROM pg_indexes WHERE schemaname = %s", (schema,))}
    assert {"claims_embedding_hnsw", "claims_paper_idx", "chat_messages_session"} <= idx
    with db.use_session(sid), db.connect() as conn:
        new_id = db.insert_claim(conn, {"paper_id": "paper-b-bbbbbb", "text": "new", "conditions": {},
                                        "sentence_ids": ["paper-b-bbbbbb:p1_s1"]}, vec(9))
        assert new_id == 5  # serial sequence moved with its table
        refs = db.assign_node_refs(conn, ["claim:5"])
        assert refs["claim:5"] == 8 and refs["claim:1"] == 2  # numbering continues; old numbers unchanged
        msg = db.insert_chat_message(conn, {"session_id": sid, "role": "user", "content": "hi"})
        assert msg["id"] == 3
    with db.use_session(sid), pytest.raises(psycopg.errors.CheckViolation), db.connect() as conn:
        conn.execute("INSERT INTO relationships (claim_a, claim_b, label) VALUES (3, 1, 'supports')")  # a < b
    with db.use_session(sid), pytest.raises(psycopg.errors.ForeignKeyViolation), db.connect() as conn:
        conn.execute("INSERT INTO sentences (id, paper_id, page, idx, text) VALUES ('x:p1_s1', 'missing', 1, 1, 't')")
    with db.use_session(sid), db.connect() as conn:  # cascades still work
        db.delete_paper(conn, "paper-d-dddddd")
        assert db.analysis_counts(conn)["impacts"] == 0


# ---------- 5-6: failures roll everything back ----------
def test_failure_just_before_commit_rolls_back_everything(legacy_db):
    before = fingerprints(legacy_db, "public")

    def boom(conn, sid):
        raise RuntimeError("simulated crash before commit")
    with pytest.raises(RuntimeError, match="simulated"):
        migrate.migrate_legacy(_before_commit=boom)
    assert_untouched(legacy_db, before)
    assert migrate.migrate_legacy()["migrated"]  # and it can simply be run again


def test_failed_session_initialisation_cannot_leave_a_committed_partial_migration(legacy_db, monkeypatch):
    before = fingerprints(legacy_db, "public")

    def broken_ddl(conn, schema):
        raise psycopg.errors.SyntaxError("simulated DDL failure")
    monkeypatch.setattr(db, "_apply_session_ddl", broken_ddl)
    with pytest.raises(psycopg.errors.SyntaxError):
        migrate.migrate_legacy()
    assert_untouched(legacy_db, before)


def test_verification_mismatch_rolls_back(legacy_db, monkeypatch):
    before = fingerprints(legacy_db, "public")
    real = migrate.table_fingerprint
    calls = {"n": 0}

    def drifting(conn, schema, table, columns=None):
        calls["n"] += 1
        fp = real(conn, schema, table, columns)
        return {**fp, "rows": fp["rows"] + 1} if schema.startswith("rl_") and table == "claims" else fp
    monkeypatch.setattr(migrate, "table_fingerprint", drifting)
    with pytest.raises(migrate.MigrationError, match="differ"):
        migrate.migrate_legacy()
    monkeypatch.setattr(migrate, "table_fingerprint", real)
    assert_untouched(legacy_db, before)


# ---------- 7-9: reruns, inconsistent states, fresh databases ----------
def test_rerun_is_a_verified_no_op_without_duplicate_sessions(legacy_db):
    sid = migrate.migrate_legacy()["session_id"]
    after = fingerprints(legacy_db, db.schema_for(sid))
    again = migrate.migrate_legacy()
    assert again["migrated"] is False and again["state"] == "migrated"
    assert again["sessions"][sid]["claims"] == 4  # verified readable, not just skipped
    assert [s["id"] for s in db.list_sessions()] == [sid]
    assert fingerprints(legacy_db, db.schema_for(sid)) == after


def test_partial_legacy_layout_is_refused_and_untouched(legacy_db):
    with psycopg.connect(legacy_db, autocommit=True) as c:
        c.execute("DROP TABLE public.chat_messages")
    with pytest.raises(migrate.MigrationError, match="missing: chat_messages"):
        migrate.migrate_legacy()
    assert layout(legacy_db)["state"] == "inconsistent"
    with psycopg.connect(legacy_db, row_factory=dict_row) as c:
        assert c.execute("SELECT count(*) AS n FROM public.claims").fetchone()["n"] == 4


@pytest.mark.parametrize("damage, problem", [
    ("DROP SCHEMA {schema} CASCADE", "schema is missing"),
    ("DROP TABLE {schema}.node_refs", "missing tables: node_refs"),
    ("CREATE SCHEMA rl_ffffffffffff", "without a registry row"),
    ("CREATE TABLE public.papers (id text)", "legacy tables in public AND"),
])
def test_inconsistent_migrated_states_are_refused(legacy_db, damage, problem):
    sid = migrate.migrate_legacy()["session_id"]
    with psycopg.connect(legacy_db, autocommit=True) as c:
        c.execute(damage.format(schema=db.schema_for(sid)))
    with pytest.raises(migrate.MigrationError, match=problem):
        migrate.migrate_legacy()


def test_fresh_empty_database_is_not_mistaken_for_a_broken_migration(fresh_db):
    report = migrate.migrate_legacy()
    assert report == {"migrated": False, "state": "fresh", "reason": report["reason"]}
    db.init_schema()  # a new installation then starts normally
    assert layout(fresh_db)["state"] == "fresh" and db.list_sessions() == []
    sid = db.create_session("First project")["id"]
    assert layout(fresh_db)["sessions"] == [sid] and migrate.migrate_legacy()["state"] == "migrated"


def test_dry_run_reports_and_changes_nothing(legacy_db):
    before = fingerprints(legacy_db, "public")
    report = migrate.migrate_legacy(dry_run=True)
    assert report["dry_run"] and report["tables"] == before and report["question"] == QUESTION
    assert_untouched(legacy_db, before)


# ---------- maintenance state ----------
def test_unmigrated_database_is_refused_by_app_startup_paths(legacy_db):
    with pytest.raises(db.LegacyLayoutError, match="python -m app.migrate"):
        db.init_schema()
    r = TestClient(main.app).get("/api/workspace", headers={"X-Session-Id": "0123456789ab"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "migration_required"
    assert TestClient(main.app).get("/api/sessions").status_code == 503


# ---------- backup ----------
def test_backup_is_restorable_and_never_overwrites(legacy_db, tmp_path, monkeypatch):
    rep = migrate.backup_database(str(tmp_path))
    path = Path(rep["path"])
    assert path.exists() and rep["bytes"] == path.stat().st_size > 0
    assert rep["restore_verified"] and rep["tables_verified"] >= len(db.SESSION_TABLES)
    assert "postgres" not in path.name and "@" not in path.name  # no connection details in file names

    class Frozen:  # same timestamp -> same file name -> must refuse, not overwrite
        @staticmethod
        def now(tz=None):
            from datetime import datetime
            return datetime(2026, 1, 1, tzinfo=tz)
    monkeypatch.setattr(migrate, "datetime", Frozen)
    first = migrate.backup_database(str(tmp_path), verify_restore=False)
    size = Path(first["path"]).stat().st_size
    with pytest.raises(migrate.BackupError, match="already exists"):
        migrate.backup_database(str(tmp_path), verify_restore=False)
    assert Path(first["path"]).stat().st_size == size


def test_cli_requires_a_backup_and_stops_if_it_fails(legacy_db, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        migrate.main([])  # --backup-dir is mandatory

    def failing(*a, **k):
        raise migrate.BackupError("disk full")
    monkeypatch.setattr(migrate, "backup_database", failing)
    before = fingerprints(legacy_db, "public")
    assert migrate.main(["--backup-dir", "/tmp/unused"]) == 1
    assert "NOTHING MIGRATED" in capsys.readouterr().out
    assert_untouched(legacy_db, before)
