"""ResearchLens API. Run from backend/: .venv/bin/uvicorn app.main:app --reload --port 8000"""
import logging
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import chat, db, discover as discovery, pipeline, question as questions
from .config import settings
from .ingest import NoExtractableTextError
from .llm import get_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="ResearchLens", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _db_guard(fn):
    try:
        return fn()
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/health")
def health():
    """Which services are configured (booleans only — never secrets)."""
    try:
        question = pipeline.current_question()
    except Exception:  # database not configured / unreachable
        question = settings.research_question
    return {"database": bool(settings.database_url), "gemini": bool(settings.gemini_api_key),
            "brightdata": settings.brightdata_configured, "question": question,
            "max_claims": settings.max_claims, "top_k": settings.top_k}


@app.get("/api/workspace")
def get_workspace():
    """The active research question and how much analysis exists under it."""
    return _db_guard(pipeline.workspace_state)


class WorkspaceRequest(BaseModel):
    question: str
    reset: bool = False        # delete everything (papers included)
    keep_papers: bool = False  # keep papers + text, clear their analysis for deliberate re-analysis


@app.put("/api/workspace")
def put_workspace(req: WorkspaceRequest):
    """Set the active question (the UI calls this only after explicit user confirmation). Changing it while
    papers exist needs keep_papers=true or reset=true; otherwise 409 and nothing changes."""
    try:
        return _db_guard(lambda: pipeline.set_workspace_question(req.question, reset=req.reset,
                                                                 keep_papers=req.keep_papers))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except pipeline.QuestionConflict as exc:
        raise HTTPException(409, {"message": str(exc), "code": "question_conflict", "current_question": exc.current,
                                  "counts": exc.counts, "options": ["keep_papers", "reset"]})


class ProposeRequest(BaseModel):
    interest: str
    previous: List[str] = []


@app.post("/api/question/propose")
def propose_question(req: ProposeRequest):
    """Gemini suggests ONE central research question from a broad interest. Writes nothing: the user must
    review, optionally edit, and confirm it (PUT /api/workspace) before it becomes the active question."""
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        return questions.propose_question(llm, req.interest, req.previous)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except questions.ProposalUnavailableError as exc:
        raise HTTPException(503, {"message": str(exc), "code": "model_unavailable"})
    except questions.ProposalRejectedError as exc:
        raise HTTPException(502, {"message": str(exc), "code": "proposal_rejected"})


@app.get("/api/graph")
def graph():
    return _db_guard(pipeline.graph_payload)


@app.get("/api/claims/{claim_id}")
def claim(claim_id: int):
    g = _db_guard(pipeline.graph_payload)
    if str(claim_id) not in g["claims"]:
        raise HTTPException(404, "claim not found")
    return g["claims"][str(claim_id)]


@app.get("/api/relationships/{rel_id}")
def relationship(rel_id: int):
    g = _db_guard(pipeline.graph_payload)
    if str(rel_id) not in g["relationships"]:
        raise HTTPException(404, "relationship not found")
    return g["relationships"][str(rel_id)]


@app.get("/api/impact/latest")
def latest_impact():
    def run():
        with db.connect() as conn:
            paper = db.latest_new_paper(conn)
        if not paper:
            raise HTTPException(404, "no new paper has been evaluated yet")
        return pipeline.impact_summary(paper["id"])
    return _db_guard(run)


UPLOAD_MODES = ("library", "evaluate")


@app.get("/api/papers/lookup")
def lookup_paper(sha256: str):
    """Reconcile an uncertain upload: is THIS exact PDF stored, and is its analysis complete?"""
    try:
        return _db_guard(lambda: pipeline.lookup_upload(sha256))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/papers")
def upload_paper(file: UploadFile = File(...), title: Optional[str] = Form(None), doi: Optional[str] = Form(None),
                 url: Optional[str] = Form(None), year: Optional[int] = Form(None),
                 mode: str = Form("evaluate")):
    """mode=library: add to the initial evidence library (extract + compare).
    mode=evaluate (default): evaluate a new paper's impact on the existing library."""
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
        return _db_guard(lambda: run(llm, data, title=title, doi=doi, url=url, year=year))
    except NoExtractableTextError as exc:
        raise HTTPException(422, str(exc))
    except (pipeline.NoQuestionError, pipeline.NoLibraryError) as exc:
        raise HTTPException(409, str(exc))
    except pipeline.PendingAnalysisError as exc:
        raise HTTPException(409, {"message": str(exc), "code": "pending_analysis"})
    except pipeline.DuplicatePaperError as exc:
        # Nothing was stored or changed; the existing paper keeps its analysis.
        raise HTTPException(409, {"message": str(exc), "code": "duplicate", "paper_id": exc.paper["id"],
                                  "title": exc.paper["title"]})
    except pipeline.ModelUnavailableError as exc:
        raise HTTPException(503, str(exc))


@app.post("/api/papers/{paper_id}/analyze")
def analyze_paper(paper_id: str):
    """User-triggered re-analysis of a stored paper under the current question (after a confirmed
    question change with keep_papers=true). Never deletes the paper or its text."""
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    try:
        return _db_guard(lambda: pipeline.analyze_stored_paper(llm, paper_id))
    except KeyError:
        raise HTTPException(404, "paper not found")
    except pipeline.AlreadyAnalyzedError as exc:
        raise HTTPException(409, {"message": str(exc), "code": "already_analyzed"})
    except (pipeline.AnalysisOrderError, pipeline.NoLibraryError, pipeline.NoQuestionError) as exc:
        raise HTTPException(409, {"message": str(exc), "code": "analysis_order"})
    except pipeline.ModelUnavailableError as exc:
        raise HTTPException(503, str(exc))


class DiscoverRequest(BaseModel):
    query: Optional[str] = None
    paper_id: Optional[str] = None


@app.post("/api/discover")
def discover(req: DiscoverRequest):
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
        return _db_guard(run)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Discovery sources unavailable: {exc}")


@app.get("/api/checks")
def checks():
    """Validation criterion 1 (citation integrity): every list must be empty."""
    def run():
        with db.connect() as conn:
            v = db.integrity_violations(conn)
        return {"ok": all(not ids for ids in v.values()), "violations": v}
    return _db_guard(run)


# ---------- research assistant (chat) ----------
class ChatContext(BaseModel):
    kind: str
    id: Optional[object] = None


class ChatRequest(BaseModel):
    message: str
    context: Optional[ChatContext] = None
    session_id: Optional[str] = None


@app.get("/api/chat/messages")
def chat_messages(session_id: Optional[str] = None):
    """Stored conversation for a session (default session until Stage D adds more)."""
    try:
        return {"session_id": chat.check_session(session_id), "messages": _db_guard(lambda: chat.history(session_id))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/api/chat")
def ask_chat(req: ChatRequest):
    """Evidence-grounded answer. Unverifiable answers are withheld (see app/chat.py)."""
    try:
        question = pipeline.active_question()
    except pipeline.NoQuestionError as exc:
        raise HTTPException(409, str(exc))
    try:
        llm = get_llm()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    context: Optional[Dict] = req.context.model_dump() if req.context else None
    try:
        return _db_guard(lambda: chat.ask(llm, question, req.message, context=context, session_id=req.session_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except chat.ChatUnavailableError as exc:
        raise HTTPException(503, str(exc))
