"""ResearchLens API. Run from backend/: .venv/bin/uvicorn app.main:app --reload --port 8000

Sessions (Stage D): every research endpoint requires the header `X-Session-Id: <12 hex>` of an existing
session. The request then runs inside `db.use_session(id)`, so every query is confined to that session's schema.
Missing, malformed or unknown ids are rejected (400/404) — there is no fallback session.
"""
import logging
from typing import Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import chat, db, discover as discovery, pipeline, question as questions
from .config import settings
from .ingest import NoExtractableTextError
from .llm import get_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="ResearchLens", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
SESSION_HEADER = "X-Session-Id"


def _db_guard(fn):
    try:
        return fn()
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc))
    except db.SessionNotFound:
        raise HTTPException(404, {"message": "This research session no longer exists.", "code": "session_not_found"})


def _check_layout() -> None:
    """Maintenance state: refuse research requests while the pre-session layout has not been migrated."""
    try:
        with db.connect_registry() as conn:
            legacy = db.legacy_tables(conn)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc))
    if legacy:
        raise HTTPException(503, {"code": "migration_required",
                                  "message": "The database still uses the single-workspace layout. Stop the backend, "
                                             "run `python -m app.migrate --backup-dir <dir>`, then restart."})


def require_session(x_session_id: Optional[str] = Header(None, alias=SESSION_HEADER)) -> str:
    """The verified session of this request. Never defaults."""
    _check_layout()
    if not x_session_id:
        raise HTTPException(400, {"message": f"Missing {SESSION_HEADER} header: select a research session.",
                                  "code": "session_required"})
    try:
        sid = db.check_session_id(x_session_id)
    except ValueError:
        raise HTTPException(400, {"message": "Invalid research session id.", "code": "invalid_session"})
    try:
        db.get_session(sid)
    except db.SessionNotFound:
        raise HTTPException(404, {"message": "This research session does not exist.", "code": "session_not_found"})
    return sid


def _in(sid: str, fn):
    """Run fn with all database access confined to session `sid`."""
    with db.use_session(sid):
        return _db_guard(fn)


def _touched(sid: str, result):
    db.touch_session(sid)
    return result


@app.get("/api/health")
def health(x_session_id: Optional[str] = Header(None, alias=SESSION_HEADER)):
    """Which services are configured (booleans only — never secrets). `question` only for a valid session."""
    question = None
    try:
        if x_session_id:
            sid = db.check_session_id(x_session_id)
            db.get_session(sid)
            with db.use_session(sid):
                question = pipeline.current_question()
    except Exception:  # unknown session / database unreachable: report services only
        question = None
    return {"database": bool(settings.database_url), "gemini": bool(settings.gemini_api_key),
            "brightdata": settings.brightdata_configured, "question": question,
            "max_claims": settings.max_claims, "top_k": settings.top_k}


# ---------- sessions ----------
def _session_payload(row: Dict) -> Dict:
    with db.connect_registry() as conn:
        summary = db.session_summary(conn, row["schema_name"])
    return {"id": row["id"], "title": row["title"], "title_source": row["title_source"],
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(), **summary}


class SessionCreate(BaseModel):
    title: Optional[str] = None  # empty: automatic title (follows the confirmed research question)


class SessionPatch(BaseModel):
    title: Optional[str] = None  # null: revert to the automatic title


def _session_errors(fn):
    try:
        _check_layout()
        return _db_guard(fn)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except db.SessionNotFound:
        raise HTTPException(404, {"message": "This research session does not exist.", "code": "session_not_found"})


@app.get("/api/sessions")
def list_sessions():
    return _session_errors(lambda: {"sessions": [_session_payload(r) for r in db.list_sessions()]})


@app.post("/api/sessions", status_code=201)
def create_session(req: SessionCreate):
    return _session_errors(lambda: _session_payload(db.create_session(req.title)))


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    return _session_errors(lambda: _session_payload(db.get_session(session_id)))


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, req: SessionPatch):
    """Rename (title_source becomes 'manual' and is never auto-updated again), or title=null to revert to auto."""
    return _session_errors(lambda: _session_payload(db.rename_session(session_id, req.title)))


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, confirm: Optional[str] = None):
    """Permanently delete one session and all its research. Requires ?confirm=<session id> (a deliberate,
    separate confirmation); other sessions are never affected."""
    def run():
        sid = db.check_session_id(session_id)
        row = db.get_session(sid)
        if confirm != sid:
            raise HTTPException(409, {"message": "Deleting a research session needs explicit confirmation "
                                                 "(?confirm=<session id>).", "code": "confirmation_required"})
        summary = _session_payload(row)
        db.delete_session(sid)
        return {"deleted": sid, "title": row["title"], "counts": summary["counts"]}
    return _session_errors(run)


# ---------- workspace (the session's central question) ----------
@app.get("/api/workspace")
def get_workspace(sid: str = Depends(require_session)):
    """The session's research question and how much analysis exists under it."""
    return _in(sid, lambda: {**pipeline.workspace_state(), "session_id": sid})


class WorkspaceRequest(BaseModel):
    question: str
    reset: bool = False        # delete everything in THIS session (papers included)
    keep_papers: bool = False  # keep papers + text, clear their analysis for deliberate re-analysis


@app.put("/api/workspace")
def put_workspace(req: WorkspaceRequest, sid: str = Depends(require_session)):
    """Set the session's question (the UI calls this only after explicit user confirmation). Changing it while
    papers exist needs keep_papers=true or reset=true; otherwise 409 and nothing changes."""
    try:
        out = _in(sid, lambda: pipeline.set_workspace_question(req.question, reset=req.reset,
                                                               keep_papers=req.keep_papers))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except pipeline.QuestionConflict as exc:
        raise HTTPException(409, {"message": str(exc), "code": "question_conflict", "current_question": exc.current,
                                  "counts": exc.counts, "options": ["keep_papers", "reset"]})
    db.apply_auto_title(sid, out["question"])  # no-op for manually named sessions
    db.touch_session(sid)
    return {**out, "session_id": sid}


class ProposeRequest(BaseModel):
    interest: str
    previous: List[str] = []


@app.post("/api/question/propose")
def propose_question(req: ProposeRequest, sid: str = Depends(require_session)):
    """Gemini suggests ONE central research question from a broad interest. Writes nothing: the user must
    review, optionally edit, and confirm it (PUT /api/workspace) before it becomes the session's question."""
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        return {**questions.propose_question(llm, req.interest, req.previous), "session_id": sid}
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except questions.ProposalUnavailableError as exc:
        raise HTTPException(503, {"message": str(exc), "code": "model_unavailable"})
    except questions.ProposalRejectedError as exc:
        raise HTTPException(502, {"message": str(exc), "code": "proposal_rejected"})


# ---------- graph and inspectors ----------
@app.get("/api/graph")
def graph(sid: str = Depends(require_session)):
    return _in(sid, lambda: {**pipeline.graph_payload(), "session_id": sid})


@app.get("/api/claims/{claim_id}")
def claim(claim_id: int, sid: str = Depends(require_session)):
    g = _in(sid, pipeline.graph_payload)
    if str(claim_id) not in g["claims"]:
        raise HTTPException(404, "claim not found")
    return g["claims"][str(claim_id)]


@app.get("/api/relationships/{rel_id}")
def relationship(rel_id: int, sid: str = Depends(require_session)):
    g = _in(sid, pipeline.graph_payload)
    if str(rel_id) not in g["relationships"]:
        raise HTTPException(404, "relationship not found")
    return g["relationships"][str(rel_id)]


# ---------- classification reviews (audit trail; stored labels are never rewritten) ----------
class ReviewProposal(BaseModel):
    target_kind: str           # relationship | impact
    target_id: int
    proposed_label: Optional[str] = None
    reason: str
    reviewer: str              # the person proposing it


class ReviewDecision(BaseModel):
    decision: str              # accepted | rejected
    decided_by: str
    note: Optional[str] = None


def _review_target_label(conn, kind: str, target_id: int) -> str:
    table = {"relationship": ("relationships", "label"), "impact": ("impacts", "impact")}.get(kind)
    if not table:
        raise HTTPException(422, "target_kind must be relationship or impact")
    row = conn.execute(f"SELECT {table[1]} AS label FROM {table[0]} WHERE id = %s", (target_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"{kind} not found in this project")
    return row["label"]


@app.get("/api/reviews")
def list_reviews(sid: str = Depends(require_session)):
    def run():
        with db.connect() as conn:
            return {"reviews": db.all_reviews(conn)}
    return _in(sid, run)


@app.post("/api/reviews", status_code=201)
def propose_review(req: ReviewProposal, sid: str = Depends(require_session)):
    """A person proposes a reclassification. It is recorded, never applied to the stored label."""
    if not req.reason.strip() or not req.reviewer.strip():
        raise HTTPException(422, "reason and reviewer are required")

    def run():
        with db.connect() as conn:
            original = _review_target_label(conn, req.target_kind, req.target_id)
            rid = db.add_review(conn, {"target_kind": req.target_kind, "target_id": req.target_id,
                                       "original_label": original, "proposed_label": req.proposed_label,
                                       "reason": req.reason.strip(), "reviewer_type": "human",
                                       "reviewer": req.reviewer.strip()})
            return {"id": rid, "original_label": original, "status": "proposed"}
    return _touched(sid, _in(sid, run))


@app.post("/api/reviews/{review_id}/decision")
def decide_review(review_id: int, req: ReviewDecision, sid: str = Depends(require_session)):
    """A named person accepts or rejects a proposal (automated, model or human). The relationship/impact row
    keeps its original label; the accepted review is shown next to it."""
    def run():
        with db.connect() as conn:
            try:
                row = db.decide_review(conn, review_id, req.decision, req.decided_by, req.note)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            if not row:
                raise HTTPException(409, "review not found or already decided")
            return row
    return _touched(sid, _in(sid, run))


@app.get("/api/impact/latest")
def latest_impact(sid: str = Depends(require_session)):
    def run():
        with db.connect() as conn:
            paper = db.latest_new_paper(conn)
        if not paper:
            raise HTTPException(404, "no new paper has been evaluated yet")
        return pipeline.impact_summary(paper["id"])
    return _in(sid, run)


# ---------- uploads and analysis ----------
UPLOAD_MODES = ("library", "evaluate")


@app.get("/api/papers/lookup")
def lookup_paper(sha256: str, sid: str = Depends(require_session)):
    """Reconcile an uncertain upload: is THIS exact PDF stored in this session, and is its analysis complete?"""
    try:
        return _in(sid, lambda: pipeline.lookup_upload(sha256))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/papers")
def upload_paper(file: UploadFile = File(...), title: Optional[str] = Form(None), doi: Optional[str] = Form(None),
                 url: Optional[str] = Form(None), year: Optional[int] = Form(None),
                 mode: str = Form("evaluate"), sid: str = Depends(require_session)):
    """mode=library: add to the session's evidence library (extract + compare).
    mode=evaluate (default): evaluate a new paper's impact on the session's existing library."""
    if mode not in UPLOAD_MODES:
        raise HTTPException(422, f"mode must be one of {list(UPLOAD_MODES)}")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "PDF is larger than 30 MB")
    if not data:
        raise HTTPException(422, "Empty file")
    run = pipeline.add_library_paper if mode == "library" else pipeline.process_new_paper
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        result = _in(sid, lambda: run(llm, data, title=title, doi=doi, url=url, year=year))
    except NoExtractableTextError as exc:
        raise HTTPException(422, str(exc))
    except (pipeline.NoQuestionError, pipeline.NoLibraryError) as exc:
        raise HTTPException(409, str(exc))
    except pipeline.PendingAnalysisError as exc:
        raise HTTPException(409, {"message": str(exc), "code": "pending_analysis"})
    except pipeline.DuplicatePaperError as exc:
        # Nothing was stored or changed; the existing paper (in THIS session) keeps its analysis.
        raise HTTPException(409, {"message": str(exc), "code": "duplicate", "paper_id": exc.paper["id"],
                                  "title": exc.paper["title"]})
    except pipeline.ModelUnavailableError as exc:
        raise HTTPException(503, str(exc))
    return _touched(sid, {**result, "session_id": sid})


@app.post("/api/papers/{paper_id}/analyze")
def analyze_paper(paper_id: str, sid: str = Depends(require_session)):
    """User-triggered re-analysis of a stored paper under the session's current question."""
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        result = _in(sid, lambda: pipeline.analyze_stored_paper(llm, paper_id))
    except KeyError:
        raise HTTPException(404, "paper not found")
    except pipeline.AlreadyAnalyzedError as exc:
        raise HTTPException(409, {"message": str(exc), "code": "already_analyzed"})
    except (pipeline.AnalysisOrderError, pipeline.NoLibraryError, pipeline.NoQuestionError) as exc:
        raise HTTPException(409, {"message": str(exc), "code": "analysis_order"})
    except pipeline.ModelUnavailableError as exc:
        raise HTTPException(503, str(exc))
    return _touched(sid, {**result, "session_id": sid})


class DiscoverRequest(BaseModel):
    query: Optional[str] = None
    paper_id: Optional[str] = None


@app.post("/api/discover")
def discover(req: DiscoverRequest, sid: str = Depends(require_session)):
    """Find related research (Bright Data Scholar -> OpenAlex). Defaults to the latest new paper's title."""
    def run():
        query, paper_id = (req.query or "").strip(), req.paper_id
        with db.connect() as conn:
            paper = db.get_paper(conn, paper_id) if paper_id else db.latest_new_paper(conn)
        if paper_id and not paper:
            raise HTTPException(404, "paper not found")
        if not query:
            if not paper:
                raise HTTPException(422, "Provide a query or upload a new paper first")
            query = paper["title"]
        return discovery.discover(query, for_paper_id=paper["id"] if paper else None)
    try:
        return _in(sid, run)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Discovery sources unavailable: {exc}")


@app.get("/api/checks")
def checks(sid: str = Depends(require_session)):
    """Validation criterion 1 (citation integrity) for THIS session: every list must be empty."""
    def run():
        with db.connect() as conn:
            v = db.integrity_violations(conn)
        return {"ok": all(not ids for ids in v.values()), "violations": v, "session_id": sid}
    return _in(sid, run)


# ---------- research assistant (chat) ----------
class ChatContext(BaseModel):
    kind: str
    id: Optional[object] = None


class ChatRequest(BaseModel):
    message: str
    context: Optional[ChatContext] = None
    session_id: Optional[str] = None  # optional echo; must equal the X-Session-Id header when given


def _same_session(sid: str, claimed: Optional[str]) -> None:
    if claimed is not None and claimed != sid:
        raise HTTPException(422, {"message": "session_id does not match the X-Session-Id header.",
                                  "code": "session_mismatch"})


@app.get("/api/chat/messages")
def chat_messages(session_id: Optional[str] = None, sid: str = Depends(require_session)):
    """The session's stored conversation."""
    _same_session(sid, session_id)
    return {"session_id": sid, "messages": _in(sid, lambda: chat.history(sid))}


@app.post("/api/chat")
def ask_chat(req: ChatRequest, sid: str = Depends(require_session)):
    """Evidence-grounded answer from THIS session's evidence only. Unverifiable answers are withheld."""
    _same_session(sid, req.session_id)
    try:
        question = _in(sid, pipeline.active_question)
    except pipeline.NoQuestionError as exc:
        raise HTTPException(409, str(exc))
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    context: Optional[Dict] = req.context.model_dump() if req.context else None
    try:
        result = _in(sid, lambda: chat.ask(llm, question, req.message, context=context, session_id=sid))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except chat.ChatUnavailableError as exc:
        raise HTTPException(503, str(exc))
    return _touched(sid, result)
