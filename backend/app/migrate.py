"""One-time, transactional migration from the single-workspace layout to research sessions (Stage D).

Before: the research tables (papers, sentences, claims, ...) live in the public schema.
After:  they live, unchanged, in the schema of ONE session ("rl_<id>"), registered in public.sessions.

How: `ALTER TABLE public.<t> SET SCHEMA rl_<id>` for every research table. Tables move with their indexes,
constraints and serial sequences; no row is copied or rewritten, so ids, node numbers, citations and embeddings
are preserved exactly.

Atomicity — ONE transaction, committed only when everything below succeeded:
  1. lock: advisory lock + ACCESS EXCLUSIVE locks on every legacy table (waits for in-flight application
     transactions, then blocks all reads/writes; `lock_timeout` makes it fail fast instead of hanging);
  2. classify the database (fresh / legacy / migrated / inconsistent); only a complete legacy layout migrates;
  3. fingerprint every table (row count + md5 over all rows);
  4. create the registry (schema_public.sql), the session row and schema; move the tables;
  5. apply the per-session DDL (schema.sql) to the moved tables — the same idempotent upgrade init_schema runs;
  6. verify: fingerprints identical, nothing left in public, and the session readable through the normal
     session-scoped interface queries (analysis counts, graph tables) on this same connection.
  Any failure rolls the whole transaction back: the database is exactly as before. Nothing runs after commit.

Required operating sequence for a real database (see `main`):
  a. stop the SpiderMonkey backend (no application writes during the migration);
  b. `python -m app.migrate --backup-dir DIR --dry-run`  → backup + restore check + report, changes nothing;
  c. `python -m app.migrate --backup-dir DIR`            → backup + restore check, then migrate;
  d. start the Stage D backend. Until the migration has run, the Stage D backend refuses research requests
     (maintenance state, HTTP 503) instead of touching the legacy tables.
"""
import argparse
import json
import logging
import os
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from . import db
from .config import BACKEND

log = logging.getLogger(__name__)

LOCK_TIMEOUT = "15s"

class MigrationError(RuntimeError):
    """Refused or failed; the transaction was rolled back and nothing changed."""

class BackupError(RuntimeError):
    """The backup could not be created or verified; the migration must not run."""

# ---------- inspection ----------
def table_columns(conn, schema: str, table: str) -> List[str]:
    rows = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                        "ORDER BY ordinal_position", (schema, table)).fetchall()
    return [r["column_name"] for r in rows]

def table_fingerprint(conn, schema: str, table: str, columns: Optional[List[str]] = None) -> Dict:
    """Row count + md5 over every row. With `columns`, only those columns are hashed (in that order), so a
    verification can compare exactly the original data even after an additive column was added."""
    ident = sql.Identifier(schema, table)
    if columns:
        row_expr = sql.SQL("ROW({})::text").format(sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c))
                                                                       for c in columns))
    else:
        row_expr = sql.SQL("t::text")
    q = sql.SQL("SELECT count(*) AS n, md5(coalesce(string_agg(r, '|' ORDER BY r), '')) AS h "
                "FROM (SELECT {} AS r FROM {} AS t) x").format(row_expr, ident)
    row = conn.execute(q).fetchone()
    return {"rows": int(row["n"]), "md5": row["h"]}

def _tables_in(conn, schema: str) -> List[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)).fetchall()
    return sorted(r["table_name"] for r in rows)

def inspect_layout(conn) -> Dict:
    """Classify the database. state ∈ fresh | legacy | migrated | inconsistent (with `problems`)."""
    legacy = db.legacy_tables(conn)
    has_registry = bool(conn.execute("SELECT to_regclass('public.sessions') AS r").fetchone()["r"])
    sessions = conn.execute("SELECT id, schema_name FROM public.sessions ORDER BY created_at").fetchall() if has_registry else []
    rl_schemas = sorted(r["nspname"] for r in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname ~ '^rl_[a-f0-9]{12}$'"))
    problems = []
    if legacy:
        missing = sorted(set(db.SESSION_TABLES) - set(legacy))
        if missing:
            problems.append(f"partial legacy layout in public (missing: {', '.join(missing)})")
        if sessions or rl_schemas:
            problems.append("legacy tables in public AND session schemas/registry rows exist")
    for s in sessions:
        if s["schema_name"] != db.schema_for(s["id"]):
            problems.append(f"session {s['id']} has unexpected schema name")
        elif s["schema_name"] not in rl_schemas:
            problems.append(f"session {s['id']} is registered but its schema is missing")
        else:
            gone = sorted(set(db.SESSION_TABLES) - set(_tables_in(conn, s["schema_name"])))
            if gone:
                problems.append(f"session {s['id']} is missing tables: {', '.join(gone)}")
    orphans = sorted(set(rl_schemas) - {s["schema_name"] for s in sessions})
    if orphans:
        problems.append(f"session schemas without a registry row: {', '.join(orphans)}")
    if problems:
        state = "inconsistent"
    elif legacy:
        state = "legacy"
    elif sessions:
        state = "migrated"
    else:
        state = "fresh"
    return {"state": state, "problems": problems, "legacy_tables": legacy, "has_registry": has_registry,
            "sessions": [s["id"] for s in sessions]}

def _usable(conn, schema: str) -> Dict:
    """Read the session through the normal session-scoped queries (same connection/transaction)."""
    conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(schema)))
    counts = db.analysis_counts(conn)
    db.get_question(conn)
    db.all_relationships(conn)
    db.node_ref_map(conn)
    conn.execute("SELECT id FROM claims WHERE embedding IS NOT NULL ORDER BY embedding <=> "
                 "(SELECT embedding FROM claims WHERE embedding IS NOT NULL LIMIT 1) LIMIT 1")
    conn.execute("SET LOCAL search_path TO public")
    return counts

def _connect(url: Optional[str] = None):
    return psycopg.connect(url or db._url(), row_factory=dict_row)

# ---------- migration ----------
def migrate_legacy(title: Optional[str] = None, dry_run: bool = False,
                   _before_commit: Optional[Callable] = None) -> Dict:
    """Move the legacy public research tables into a new session, atomically. Returns a verification report."""
    with _connect() as conn:  # psycopg: one transaction until commit(); leaving with an exception rolls back
        conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('researchlens-session-migration'))")
        layout = inspect_layout(conn)
        if layout["state"] == "inconsistent":
            conn.rollback()
            raise MigrationError("Inconsistent database layout; nothing was changed: " + "; ".join(layout["problems"]))
        if layout["state"] == "fresh":
            conn.rollback()
            return {"migrated": False, "state": "fresh", "reason": "No legacy research tables and no sessions: "
                    "a new database; nothing to migrate."}
        if layout["state"] == "migrated":
            report = {"migrated": False, "state": "migrated", "reason": "Already migrated; nothing changed.",
                      "sessions": {}}
            for sid in layout["sessions"]:
                report["sessions"][sid] = _usable(conn, db.schema_for(sid))
            conn.rollback()
            return report

        # state == "legacy": block every application read/write of the legacy tables until commit.
        conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(
            sql.SQL(", ").join(sql.Identifier("public", t) for t in db.SESSION_TABLES)))
        original_columns = {t: table_columns(conn, "public", t) for t in db.SESSION_TABLES}
        before = {t: table_fingerprint(conn, "public", t, original_columns[t]) for t in db.SESSION_TABLES}
        q = conn.execute("SELECT question FROM public.workspace WHERE id = 1").fetchone()
        question = q["question"] if q else None
        if dry_run:
            conn.rollback()
            return {"migrated": False, "state": "legacy", "dry_run": True, "question": question, "tables": before,
                    "would_create_session_title": db.clean_title(title) if title and title.strip() else db.auto_title(question)}

        conn.execute((BACKEND / "schema_public.sql").read_text())
        sid = secrets.token_hex(6)
        schema = db.schema_for(sid)
        manual = bool(title and title.strip())
        conn.execute("INSERT INTO public.sessions (id, schema_name, title, title_source) VALUES (%s, %s, %s, %s)",
                     (sid, schema, db.clean_title(title) if manual else db.auto_title(question),
                      "manual" if manual else "auto"))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for t in db.SESSION_TABLES:
            conn.execute(sql.SQL("ALTER TABLE {} SET SCHEMA {}").format(sql.Identifier("public", t), sql.Identifier(schema)))
        db._apply_session_ddl(conn, schema)  # same idempotent upgrade init_schema applies; inside this transaction
        # Compare exactly the original columns: the session DDL may add new (empty) columns, which is not a change
        # to existing research data.
        after = {t: table_fingerprint(conn, schema, t, original_columns[t]) for t in db.SESSION_TABLES}
        if after != before:
            raise MigrationError(f"Verification failed: table contents differ after the move. before={before} after={after}")
        final = inspect_layout(conn)
        if final["state"] != "migrated" or final["sessions"] != [sid]:
            raise MigrationError(f"Verification failed: unexpected layout after the move: {final}")
        counts = _usable(conn, schema)
        if _before_commit:
            _before_commit(conn, sid)  # test hook: simulate a failure at the last moment
        conn.commit()
    log.info("migrated legacy workspace into session %s", sid)
    return {"migrated": True, "state": "migrated", "session_id": sid, "schema": schema, "question": question,
            "tables": after, "verified_identical": True, "counts": counts}

# ---------- backup ----------
def _pg_tool(name: str) -> str:
    """pg_dump / pg_restore: RL_PG_BIN dir, else the binaries bundled with pgserver (local dev), else PATH."""
    candidates = []
    if os.environ.get("RL_PG_BIN"):
        candidates.append(Path(os.environ["RL_PG_BIN"]) / name)
    try:
        import pgserver  # optional, local development only
        candidates.append(Path(pgserver.__file__).parent / "pginstall" / "bin" / name)
    except ImportError:
        pass
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for c in candidates:
        if c.exists():
            return str(c)
    raise BackupError(f"{name} not found (set RL_PG_BIN to the PostgreSQL bin directory).")

def _with_database(url: str, name: str) -> str:
    """Same server/credentials, different database name (keeps any query string, e.g. a socket host)."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))

def backup_database(backup_dir: str, url: Optional[str] = None, verify_restore: bool = True) -> Dict:
    """pg_dump (custom format) to a NEW timestamped file — never overwrites — then verify it is non-empty and
    restores into a temporary database whose table fingerprints match the source. The temp database is dropped."""
    url = url or db._url()
    out_dir = Path(backup_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = out_dir / f"researchlens-pre-sessions-{stamp}.dump"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)  # refuse to overwrite anything
        os.close(fd)
    except FileExistsError as exc:
        raise BackupError(f"Backup file already exists: {path}") from exc
    res = subprocess.run([_pg_tool("pg_dump"), "--format=custom", "--file", str(path), url],
                         capture_output=True, text=True)
    if res.returncode != 0 or path.stat().st_size == 0:
        raise BackupError(f"pg_dump failed (exit {res.returncode}): {res.stderr.strip()[:500]}")
    report = {"path": str(path), "bytes": path.stat().st_size, "restore_verified": False}
    if not verify_restore:
        return report
    with _connect(url) as conn:
        src = {f"{s}.{t}": table_fingerprint(conn, s, t) for s, t in _research_tables(conn)}
    temp = f"rl_restore_check_{secrets.token_hex(4)}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(temp)))
    try:
        turl = _with_database(url, temp)
        res = subprocess.run([_pg_tool("pg_restore"), "--no-owner", "--dbname", turl, str(path)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise BackupError(f"pg_restore check failed (exit {res.returncode}): {res.stderr.strip()[:500]}")
        with _connect(turl) as conn:
            restored = {f"{s}.{t}": table_fingerprint(conn, s, t) for s, t in _research_tables(conn)}
        if restored != src:
            raise BackupError("Restored backup does not match the source database.")
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(temp)))
    report.update(restore_verified=True, tables_verified=len(src))
    return report

def _research_tables(conn) -> List:
    rows = conn.execute(
        """SELECT table_schema, table_name FROM information_schema.tables
           WHERE table_type = 'BASE TABLE' AND (table_schema = 'public' OR table_schema ~ '^rl_[a-f0-9]{12}$')
           ORDER BY 1, 2""").fetchall()
    return [(r["table_schema"], r["table_name"]) for r in rows]

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Migrate the single-workspace database to research sessions.")
    ap.add_argument("--backup-dir", required=True, help="directory for a NEW verified pg_dump backup (required)")
    ap.add_argument("--dry-run", action="store_true", help="backup + report only; change nothing")
    ap.add_argument("--title", help="session title (default: derived from the research question)")
    args = ap.parse_args(argv)
    try:
        backup = backup_database(args.backup_dir)
        print(json.dumps({"backup": backup}, indent=1))
        report = migrate_legacy(title=args.title, dry_run=args.dry_run)
    except (BackupError, MigrationError) as exc:
        print(f"STOPPED, NOTHING MIGRATED: {exc}")
        return 1
    print(json.dumps(report, indent=1, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())