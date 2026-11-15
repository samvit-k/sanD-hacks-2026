"""Tiger Data (PostgreSQL + pgvector) access. Plain SQL, one short connection per step.

Session isolation (Stage D): every research session owns a PostgreSQL schema "rl_<id>" holding its own copy of
the research tables (schema.sql). `connect()` only works inside `use_session(id)` and pins the connection's
search_path to that schema (+ public for the vector type and the shared discovery cache). There is no default
session: a connection without a session raises NoSessionError. Registry operations use `connect_registry()`.
"""
import contextvars
import re
import secrets
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import BACKEND, settings

SESSION_ID_RE = re.compile(r"^[a-f0-9]{12}$")
# Tables owned by a session (moved out of public by the one-time legacy migration, see app/migrate.py).
SESSION_TABLES = ("workspace", "papers", "sentences", "claims", "relationships", "impacts", "discovery_links",
                  "node_refs", "chat_messages")
AUTO_TITLE = "Untitled research"
MAX_TITLE_LEN = 120

_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("researchlens_session", default=None)


class DatabaseNotConfigured(RuntimeError):
    pass


class NoSessionError(RuntimeError):
    """A research-data query was attempted without a verified session. Never falls back to a default."""


class SessionNotFound(LookupError):
    pass


class LegacyLayoutError(RuntimeError):
    """Research tables still live in the public schema: run the one-time migration (python -m app.migrate)."""


def _url() -> str:
    if not settings.database_url:
        raise DatabaseNotConfigured("DATABASE_URL is not set in .env")
    return settings.database_url


def check_session_id(session_id: Optional[str]) -> str:
    sid = (session_id or "").strip()
    if not SESSION_ID_RE.match(sid):
        raise ValueError("Invalid research session id.")
    return sid


def schema_for(session_id: str) -> str:
    return f"rl_{check_session_id(session_id)}"


def current_session() -> Optional[str]:
    schema = _session.get()
    return schema[3:] if schema else None


@contextmanager
def use_session(session_id: str):
    """Bind research-data access in this context (thread / request) to one session."""
    token = _session.set(schema_for(session_id))
    try:
        yield session_id
    finally:
        _session.reset(token)


def _open():
    conn = psycopg.connect(_url(), row_factory=dict_row)
    return conn


@contextmanager
def _transaction(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def connect():
    """Connection scoped to the current session's schema (see use_session)."""
    schema = _session.get()
    if not schema:
        raise NoSessionError("No research session selected for this operation.")
    conn = _open()
    with _transaction(conn):
        exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
        if not exists:
            raise SessionNotFound(schema[3:])
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        register_vector(conn)
        yield conn


@contextmanager
def connect_registry():
    """Connection for the shared registry (public schema only; no research tables are visible here)."""
    conn = _open()
    with _transaction(conn):
        conn.execute("SET search_path TO public")
        yield conn


def legacy_tables(conn) -> List[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                        "AND table_name = ANY(%s)", (list(SESSION_TABLES),)).fetchall()
    return sorted(r["table_name"] for r in rows)


def _apply_session_ddl(conn, schema: str) -> None:
    conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    conn.execute((BACKEND / "schema.sql").read_text())
    conn.execute("SET search_path TO public")


def init_schema() -> None:
    """Apply the shared DDL, then (idempotently) the per-session DDL to every registered session.
    Refuses to run while research tables still live in public (pre-Stage-D layout): migrate first."""
    with psycopg.connect(_url(), autocommit=True, row_factory=dict_row) as conn:
        if legacy_tables(conn):
            raise LegacyLayoutError("This database still has research tables in the public schema "
                                    f"({', '.join(legacy_tables(conn))}). Run `python -m app.migrate` first.")
        conn.execute((BACKEND / "schema_public.sql").read_text())
        for row in conn.execute("SELECT schema_name FROM public.sessions").fetchall():
            _apply_session_ddl(conn, row["schema_name"])


# ---------- session registry ----------
def auto_title(question: Optional[str]) -> str:
    q = " ".join((question or "").split()).rstrip("?").strip()
    if not q:
        return AUTO_TITLE
    if len(q) <= 60:
        return q
    cut = q[:57].rsplit(" ", 1)[0]
    return f"{cut}…"


def clean_title(title: Optional[str]) -> str:
    t = " ".join((title or "").split())
    if not t:
        raise ValueError("The session title must not be empty.")
    if len(t) > MAX_TITLE_LEN:
        raise ValueError(f"The session title must be at most {MAX_TITLE_LEN} characters.")
    return t


def create_session(title: Optional[str] = None, session_id: Optional[str] = None) -> Dict:
    """New, empty research session: registry row + its own schema with all research tables (one transaction)."""
    sid = check_session_id(session_id) if session_id else secrets.token_hex(6)
    manual = bool(title and title.strip())
    with connect_registry() as conn:
        row = conn.execute(
            "INSERT INTO public.sessions (id, schema_name, title, title_source) VALUES (%s, %s, %s, %s) RETURNING *",
            (sid, schema_for(sid), clean_title(title) if manual else AUTO_TITLE, "manual" if manual else "auto"),
        ).fetchone()
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_for(sid))))
        _apply_session_ddl(conn, schema_for(sid))
    return row


def get_session(session_id: str) -> Dict:
    sid = check_session_id(session_id)
    with connect_registry() as conn:
        row = conn.execute("SELECT * FROM public.sessions WHERE id = %s", (sid,)).fetchone()
    if not row:
        raise SessionNotFound(sid)
    return row


def list_sessions() -> List[Dict]:
    with connect_registry() as conn:
        return conn.execute("SELECT * FROM public.sessions ORDER BY updated_at DESC, created_at DESC").fetchall()


def rename_session(session_id: str, title: Optional[str]) -> Dict:
    """A manual title is never changed automatically again. title=None reverts to the automatic title."""
    sid = check_session_id(session_id)
    new_title = None if title is None else clean_title(title)
    with connect_registry() as conn:
        if not conn.execute("SELECT 1 FROM public.sessions WHERE id = %s FOR UPDATE", (sid,)).fetchone():
            raise SessionNotFound(sid)
        if new_title is None:
            q = conn.execute(sql.SQL("SELECT question FROM {}.workspace WHERE id = 1").format(
                sql.Identifier(schema_for(sid)))).fetchone()
            row = conn.execute("UPDATE public.sessions SET title = %s, title_source = 'auto', updated_at = now() "
                               "WHERE id = %s RETURNING *", (auto_title(q and q["question"]), sid)).fetchone()
        else:
            row = conn.execute("UPDATE public.sessions SET title = %s, title_source = 'manual', updated_at = now() "
                               "WHERE id = %s RETURNING *", (new_title, sid)).fetchone()
    if not row:
        raise SessionNotFound(sid)
    return row


def apply_auto_title(session_id: str, question: Optional[str]) -> None:
    """Follow the confirmed question unless the user named the session themselves."""
    with connect_registry() as conn:
        conn.execute("UPDATE public.sessions SET title = %s, updated_at = now() "
                     "WHERE id = %s AND title_source = 'auto'", (auto_title(question), check_session_id(session_id)))


def touch_session(session_id: str) -> None:
    with connect_registry() as conn:
        conn.execute("UPDATE public.sessions SET updated_at = now() WHERE id = %s", (check_session_id(session_id),))


def delete_session(session_id: str) -> None:
    """Permanently drop the session's schema (all its research data) and its registry row, in one transaction.
    Other sessions are untouched: their data lives in other schemas."""
    sid = check_session_id(session_id)
    with connect_registry() as conn:
        row = conn.execute("DELETE FROM public.sessions WHERE id = %s RETURNING schema_name", (sid,)).fetchone()
        if not row:
            raise SessionNotFound(sid)
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(row["schema_name"])))


def session_summary(conn, schema: str) -> Dict:
    """Question and counts of one session, read with fully qualified names (registry connection)."""
    q = sql.Identifier(schema)
    row = conn.execute(sql.SQL(
        """SELECT (SELECT question FROM {s}.workspace WHERE id = 1) AS question,
                  (SELECT count(*) FROM {s}.papers) AS papers,
                  (SELECT count(*) FROM {s}.claims) AS claims,
                  (SELECT count(*) FROM {s}.relationships) AS relationships,
                  (SELECT count(*) FROM {s}.papers WHERE analysis_status = 'pending') AS pending_papers,
                  (SELECT count(*) FROM {s}.chat_messages) AS chat_messages""").format(s=q)).fetchone()
    return {"question": row["question"], "counts": {k: int(row[k]) for k in
                                                     ("papers", "claims", "relationships", "pending_papers", "chat_messages")}}


# ---------- papers & sentences ----------
def delete_paper(conn, paper_id: str) -> None:
    conn.execute("DELETE FROM papers WHERE id = %s", (paper_id,))


def delete_all(conn) -> None:
    """Delete all analysis (papers and everything derived from them). Keeps the workspace question
    and the discovery cache (keyed by search query, independent of the question).
    TRUNCATE is transactional in PostgreSQL: it is undone if the surrounding transaction rolls back."""
    # The conversation refers to nodes/sentences of this analysis, so it is cleared with it.
    conn.execute("TRUNCATE papers, sentences, claims, relationships, impacts, discovery_links, node_refs, "
                 "chat_messages RESTART IDENTITY CASCADE")


def clear_analysis(conn) -> int:
    """Delete everything that was derived under the current question (claims and, by cascade, their
    relationships and impacts) but KEEP the papers, their sentence index, discovery links, the chat
    history and node numbers. Papers become 'pending' until they are deliberately re-analyzed.
    Claim ids are serial, so re-extracted claims get new ids and new node numbers; retired numbers are
    never reused. Returns the number of papers now pending."""
    conn.execute("DELETE FROM impacts")
    conn.execute("DELETE FROM relationships")
    conn.execute("DELETE FROM claims")
    return conn.execute("UPDATE papers SET analysis_status = 'pending', relevance = NULL").rowcount


def delete_claims_for_paper(conn, paper_id: str) -> None:
    """Leftovers of an interrupted re-analysis (cascades to their relationships/impacts)."""
    conn.execute("DELETE FROM claims WHERE paper_id = %s", (paper_id,))


def pending_papers(conn) -> List[Dict]:
    """Papers awaiting analysis under the current question, in the order they must be analyzed:
    library papers first (oldest first), then evaluated papers (their impact needs the library)."""
    return conn.execute(
        """SELECT id, title, is_new, analysis_status FROM papers WHERE analysis_status <> 'complete'
           ORDER BY is_new, created_at, id"""
    ).fetchall()


# ---------- chat ----------
def insert_chat_message(conn, m: Dict) -> Dict:
    return conn.execute(
        """INSERT INTO chat_messages (session_id, role, content, context, cited_sentence_ids,
                                      insufficient_evidence, guardrail, question)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (m["session_id"], m["role"], m["content"], Jsonb(m.get("context")) if m.get("context") else None,
         m.get("cited_sentence_ids") or [], bool(m.get("insufficient_evidence")), m.get("guardrail"),
         m.get("question")),
    ).fetchone()


def chat_messages(conn, session_id: str, limit: int = 200, question: Optional[str] = None) -> List[Dict]:
    """The session's messages, oldest first. With `question`, only messages written under that question.
    The table lives in the session's own schema, so every row belongs to this session; `session_id` is not used
    as a filter (rows migrated from the single-workspace layout carry the label 'default')."""
    if question is None:
        rows = conn.execute("SELECT * FROM chat_messages ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM chat_messages WHERE question = %s ORDER BY id DESC LIMIT %s",
                            (question, limit)).fetchall()
    return list(reversed(rows))


def node_ref_map(conn) -> Dict[str, int]:
    return {r["node_id"]: r["num"] for r in conn.execute("SELECT node_id, num FROM node_refs")}


def nearest_claims(conn, embedding, k: int) -> List[int]:
    """pgvector cosine search over all stored claims."""
    rows = conn.execute(
        "SELECT id FROM claims WHERE embedding IS NOT NULL ORDER BY embedding <=> %s LIMIT %s",
        (np.asarray(embedding, dtype=np.float32), k),
    ).fetchall()
    return [r["id"] for r in rows]


def paper_by_content(conn, sha256: str, sha1_6: Optional[str] = None) -> Optional[Dict]:
    """The stored paper made from exactly this PDF. Matches the full SHA-256 recorded at upload; papers
    stored before that column existed (content_sha256 IS NULL) fall back to the sha1 suffix of their id."""
    row = conn.execute("SELECT * FROM papers WHERE content_sha256 = %s LIMIT 1", (sha256,)).fetchone()
    if row or not sha1_6:
        return row
    return conn.execute("SELECT * FROM papers WHERE content_sha256 IS NULL AND right(id, 7) = %s LIMIT 1",
                        ("-" + sha1_6,)).fetchone()


# ---------- stable node display numbers ----------
def assign_node_refs(conn, ordered_node_ids: List[str]) -> Dict[str, int]:
    """Give each not-yet-numbered node the next number, in the given order; return the full mapping.
    Existing numbers are never changed, so adding papers/claims never renumbers earlier nodes."""
    existing = {r["node_id"]: r["num"] for r in conn.execute("SELECT node_id, num FROM node_refs")}
    for node_id in ordered_node_ids:
        if node_id not in existing:
            row = conn.execute("INSERT INTO node_refs (node_id) VALUES (%s) ON CONFLICT (node_id) DO NOTHING "
                               "RETURNING num", (node_id,)).fetchone()
            if row:
                existing[node_id] = row["num"]
    missing = [n for n in ordered_node_ids if n not in existing]  # inserted concurrently by another request
    if missing:
        for r in conn.execute("SELECT node_id, num FROM node_refs WHERE node_id = ANY(%s)", (missing,)):
            existing[r["node_id"]] = r["num"]
    return existing


# ---------- workspace (single active research question) ----------
def get_question(conn) -> Optional[str]:
    row = conn.execute("SELECT question FROM workspace WHERE id = 1").fetchone()
    return row["question"] if row else None


def set_question(conn, question: str) -> None:
    conn.execute(
        """INSERT INTO workspace (id, question) VALUES (1, %s)
           ON CONFLICT (id) DO UPDATE SET question = EXCLUDED.question, updated_at = now()""",
        (question,),
    )


def analysis_counts(conn) -> Dict[str, int]:
    row = conn.execute(
        """SELECT (SELECT count(*) FROM papers) AS papers,
                  (SELECT count(*) FROM papers WHERE NOT is_new) AS library_papers,
                  (SELECT count(*) FROM papers WHERE is_new) AS evaluated_papers,
                  (SELECT count(*) FROM claims) AS claims,
                  (SELECT count(*) FROM claims c JOIN papers p ON p.id = c.paper_id WHERE NOT p.is_new)
                      AS library_claims,
                  (SELECT count(*) FROM relationships) AS relationships,
                  (SELECT count(*) FROM impacts) AS impacts,
                  (SELECT count(*) FROM papers WHERE analysis_status = 'pending') AS pending_papers"""
    ).fetchone()
    return {k: int(v) for k, v in row.items()}


def insert_paper(conn, p: Dict) -> None:
    conn.execute(
        """INSERT INTO papers (id, title, doi, url, year, access_level, license, is_new, content_sha256,
                                analysis_status)
           VALUES (%(id)s, %(title)s, %(doi)s, %(url)s, %(year)s, %(access_level)s, %(license)s, %(is_new)s,
                   %(content_sha256)s, %(analysis_status)s)""",
        {"doi": None, "url": None, "year": None, "license": None, "is_new": False, "access_level": "full",
         "content_sha256": None, "analysis_status": "complete", **p},
    )


def set_analysis_status(conn, paper_id: str, status: str) -> None:
    conn.execute("UPDATE papers SET analysis_status = %s WHERE id = %s", (status, paper_id))


def insert_sentences(conn, sentences: List[Dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sentences (id, paper_id, page, idx, section, text) "
            "VALUES (%(id)s, %(paper_id)s, %(page)s, %(idx)s, %(section)s, %(text)s)",
            [{"section": "front", **s} for s in sentences],
        )


def get_paper(conn, paper_id: str) -> Optional[Dict]:
    return conn.execute("SELECT * FROM papers WHERE id = %s", (paper_id,)).fetchone()


def all_papers(conn) -> List[Dict]:
    return conn.execute("SELECT * FROM papers ORDER BY is_new, created_at, id").fetchall()


def latest_new_paper(conn) -> Optional[Dict]:
    return conn.execute("SELECT * FROM papers WHERE is_new ORDER BY created_at DESC LIMIT 1").fetchone()


def library_dois(conn) -> List[str]:
    return [r["doi"].lower() for r in conn.execute("SELECT doi FROM papers WHERE doi IS NOT NULL").fetchall()]


def set_relevance(conn, paper_id: str, relevance: Optional[float]) -> None:
    conn.execute("UPDATE papers SET relevance = %s WHERE id = %s", (relevance, paper_id))


def paper_sentences(conn, paper_id: str) -> List[Dict]:
    return conn.execute(
        "SELECT id, paper_id, page, idx, section, text FROM sentences WHERE paper_id = %s ORDER BY page, idx",
        (paper_id,),
    ).fetchall()


def sentences_by_ids(conn, ids: Iterable[str]) -> Dict[str, Dict]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, paper_id, page, idx, section, text FROM sentences WHERE id = ANY(%s)", (ids,)
    ).fetchall()
    return {r["id"]: r for r in rows}


def stored_paper_map(conn, ids: Iterable[str]) -> Dict[str, str]:
    """sentence id -> stored paper_id, for citation validation."""
    return {sid: r["paper_id"] for sid, r in sentences_by_ids(conn, ids).items()}


def context_sentences(conn, ids: List[str], window: int = 1) -> List[Dict]:
    """Cited sentences plus +/- `window` neighbours on the same page of the same paper."""
    if not ids:
        return []
    return conn.execute(
        """SELECT DISTINCT n.id, n.paper_id, n.page, n.idx, n.section, n.text
           FROM sentences s JOIN sentences n
             ON n.paper_id = s.paper_id AND n.page = s.page AND n.idx BETWEEN s.idx - %s AND s.idx + %s
           WHERE s.id = ANY(%s)
           ORDER BY n.page, n.idx""",
        (window, window, list(ids)),
    ).fetchall()


# ---------- claims ----------
def insert_claim(conn, c: Dict, embedding: List[float]) -> int:
    row = conn.execute(
        """INSERT INTO claims (paper_id, text, conditions, sentence_ids, embedding)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (c["paper_id"], c["text"], Jsonb(c["conditions"]), c["sentence_ids"],
         np.asarray(embedding, dtype=np.float32)),
    ).fetchone()
    return row["id"]


_CLAIM_COLS = """c.id, c.paper_id, c.text, c.conditions, c.sentence_ids,
                 p.title AS paper_title, p.access_level, p.is_new"""


def claims_by_ids(conn, ids: Iterable[int]) -> Dict[int, Dict]:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT {_CLAIM_COLS} FROM claims c JOIN papers p ON p.id = c.paper_id WHERE c.id = ANY(%s)", (ids,)
    ).fetchall()
    return {r["id"]: r for r in rows}


def all_claims(conn) -> List[Dict]:
    return conn.execute(f"SELECT {_CLAIM_COLS} FROM claims c JOIN papers p ON p.id = c.paper_id ORDER BY c.id").fetchall()


def claim_ids_for_paper(conn, paper_id: str) -> List[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM claims WHERE paper_id = %s ORDER BY id", (paper_id,))]


def claim_embeddings(conn, claim_ids: List[int]) -> Dict[int, np.ndarray]:
    rows = conn.execute("SELECT id, embedding FROM claims WHERE id = ANY(%s)", (list(claim_ids),)).fetchall()
    return {r["id"]: np.asarray(r["embedding"], dtype=np.float32) for r in rows if r["embedding"] is not None}


def candidate_pairs(conn, claim_ids: List[int], top_k: int) -> List[Dict]:
    """For each claim, its top_k nearest claims from OTHER papers (cosine)."""
    if not claim_ids:
        return []
    return conn.execute(
        """SELECT c.id AS a, n.id AS b, 1 - (c.embedding <=> n.embedding) AS sim
           FROM claims c
           CROSS JOIN LATERAL (
               SELECT o.id, o.embedding FROM claims o
               WHERE o.paper_id <> c.paper_id AND o.embedding IS NOT NULL
               ORDER BY o.embedding <=> c.embedding
               LIMIT %s
           ) n
           WHERE c.id = ANY(%s) AND c.embedding IS NOT NULL""",
        (top_k, list(claim_ids)),
    ).fetchall()


# ---------- relationships & impacts ----------
def normalize_pair_order(r: Dict) -> Dict:
    """Return a copy with claim_a < claim_b (schema CHECK). If swapped, flip the per-field
    [value_a, value_b] lists in condition_diff so they still line up with claim_a / claim_b."""
    if r["claim_a"] < r["claim_b"]:
        return dict(r)
    if r["claim_a"] == r["claim_b"]:
        raise ValueError("a relationship needs two different claims")
    flipped = {k: (list(reversed(v)) if isinstance(v, list) else v) for k, v in (r.get("condition_diff") or {}).items()}
    return {**r, "claim_a": r["claim_b"], "claim_b": r["claim_a"], "condition_diff": flipped}


def existing_pairs(conn) -> set:
    return {(r["claim_a"], r["claim_b"]) for r in conn.execute("SELECT claim_a, claim_b FROM relationships")}


def upsert_relationship(conn, r: Dict) -> int:
    """Insert, or refresh the existing judgment for this claim pair. Returns the relationship id."""
    r = normalize_pair_order(r)
    row = conn.execute(
        """INSERT INTO relationships (claim_a, claim_b, label, similarity, condition_diff, explanation,
                                      key_differences, cited_sentence_ids, insufficient_evidence, model)
           VALUES (%(claim_a)s, %(claim_b)s, %(label)s, %(similarity)s, %(condition_diff)s, %(explanation)s,
                   %(key_differences)s, %(cited_sentence_ids)s, %(insufficient_evidence)s, %(model)s)
           ON CONFLICT (claim_a, claim_b) DO UPDATE SET
             label = EXCLUDED.label, similarity = EXCLUDED.similarity,
             condition_diff = EXCLUDED.condition_diff, explanation = EXCLUDED.explanation,
             key_differences = EXCLUDED.key_differences, cited_sentence_ids = EXCLUDED.cited_sentence_ids,
             insufficient_evidence = EXCLUDED.insufficient_evidence, model = EXCLUDED.model, created_at = now()
           RETURNING id""",
        {"similarity": None, "model": None, **r,
         "condition_diff": Jsonb(r.get("condition_diff") or {}), "key_differences": Jsonb(r.get("key_differences") or [])},
    ).fetchone()
    return row["id"]


def all_relationships(conn) -> List[Dict]:
    return conn.execute("SELECT * FROM relationships ORDER BY id").fetchall()


def relationships_touching(conn, claim_ids: List[int]) -> List[Dict]:
    return conn.execute(
        "SELECT * FROM relationships WHERE claim_a = ANY(%s) OR claim_b = ANY(%s) ORDER BY id",
        (list(claim_ids), list(claim_ids)),
    ).fetchall()


def upsert_impact(conn, i: Dict) -> int:
    row = conn.execute(
        """INSERT INTO impacts (new_claim_id, prior_claim_id, relationship_id, impact, rationale,
                                cited_sentence_ids, insufficient_evidence, model)
           VALUES (%(new_claim_id)s, %(prior_claim_id)s, %(relationship_id)s, %(impact)s, %(rationale)s,
                   %(cited_sentence_ids)s, %(insufficient_evidence)s, %(model)s)
           ON CONFLICT (new_claim_id, prior_claim_id) DO UPDATE SET
             relationship_id = EXCLUDED.relationship_id, impact = EXCLUDED.impact,
             rationale = EXCLUDED.rationale, cited_sentence_ids = EXCLUDED.cited_sentence_ids,
             insufficient_evidence = EXCLUDED.insufficient_evidence, model = EXCLUDED.model, created_at = now()
           RETURNING id""",
        {"relationship_id": None, "model": None, **i},
    ).fetchone()
    return row["id"]


def impacts_for_paper(conn, paper_id: str) -> List[Dict]:
    return conn.execute(
        """SELECT i.* FROM impacts i JOIN claims c ON c.id = i.new_claim_id
           WHERE c.paper_id = %s ORDER BY i.id""",
        (paper_id,),
    ).fetchall()


# ---------- discovery cache ----------
def cache_get(conn, key: str) -> Optional[Dict]:
    return conn.execute("SELECT * FROM discovery_cache WHERE query_hash = %s", (key,)).fetchone()


def cache_put(conn, key: str, query: str, source: str, results: Dict) -> None:
    conn.execute(
        """INSERT INTO discovery_cache (query_hash, query, source, results)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (query_hash) DO UPDATE SET results = EXCLUDED.results, created_at = now()""",
        (key, query, source, Jsonb(results)),
    )


def link_discovery(conn, paper_id: str, key: str) -> None:
    """Associate cached results with the paper that requested them (refreshes the timestamp)."""
    conn.execute(
        """INSERT INTO discovery_links (paper_id, query_hash) VALUES (%s, %s)
           ON CONFLICT (paper_id, query_hash) DO UPDATE SET requested_at = now()""",
        (paper_id, key),
    )


def latest_discovery_for(conn, paper_id: str) -> Optional[Dict]:
    """The most recent discovery results requested BY this paper."""
    return conn.execute(
        """SELECT c.*, l.paper_id AS for_paper_id FROM discovery_links l
           JOIN discovery_cache c ON c.query_hash = l.query_hash
           WHERE l.paper_id = %s ORDER BY l.requested_at DESC LIMIT 1""",
        (paper_id,),
    ).fetchone()


# ---------- integrity checks (validation criterion 1) ----------
INTEGRITY_SQL = {
    "claims_with_bad_citations": """
        SELECT c.id FROM claims c
        WHERE cardinality(c.sentence_ids) = 0 OR EXISTS (
            SELECT 1 FROM unnest(c.sentence_ids) sid LEFT JOIN sentences s ON s.id = sid
            WHERE s.id IS NULL OR s.paper_id <> c.paper_id)""",
    "relationships_with_bad_citations": """
        SELECT r.id FROM relationships r
        JOIN claims a ON a.id = r.claim_a JOIN claims b ON b.id = r.claim_b
        WHERE NOT r.insufficient_evidence AND (
            EXISTS (SELECT 1 FROM unnest(r.cited_sentence_ids) sid LEFT JOIN sentences s ON s.id = sid
                    WHERE s.id IS NULL OR s.paper_id NOT IN (a.paper_id, b.paper_id))
            OR NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = ANY(r.cited_sentence_ids) AND s.paper_id = a.paper_id)
            OR NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = ANY(r.cited_sentence_ids) AND s.paper_id = b.paper_id))""",
    "impacts_with_bad_citations": """
        SELECT i.id FROM impacts i
        JOIN claims n ON n.id = i.new_claim_id JOIN claims p ON p.id = i.prior_claim_id
        WHERE NOT i.insufficient_evidence AND (
            EXISTS (SELECT 1 FROM unnest(i.cited_sentence_ids) sid LEFT JOIN sentences s ON s.id = sid
                    WHERE s.id IS NULL OR s.paper_id NOT IN (n.paper_id, p.paper_id))
            OR NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = ANY(i.cited_sentence_ids) AND s.paper_id = n.paper_id)
            OR NOT EXISTS (SELECT 1 FROM sentences s WHERE s.id = ANY(i.cited_sentence_ids) AND s.paper_id = p.paper_id))""",
    "insufficient_with_citations_shown": """
        SELECT id FROM relationships WHERE insufficient_evidence AND cardinality(cited_sentence_ids) > 0
        UNION ALL SELECT id FROM impacts WHERE insufficient_evidence AND cardinality(cited_sentence_ids) > 0""",
}


def integrity_violations(conn) -> Dict[str, List[int]]:
    return {name: [r["id"] for r in conn.execute(sql).fetchall()] for name, sql in INTEGRITY_SQL.items()}
