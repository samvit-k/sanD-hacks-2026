"""End-to-end pipeline: ingest -> extract -> embed -> top-k match -> compare -> (new paper) impact.

Database work happens in short transactions; Gemini calls run in a small thread pool between them.
"""
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import db, ingest
from .compare import (apply_impact_guardrail, apply_pair_guardrail, build_impact_prompt, build_pair_prompt,
                      condition_diff, dedupe_pairs)
from .config import settings
from .contracts import ExtractionResult, ImpactJudgment, PairJudgment
from .extract import build_extraction_prompt, validate_claims
from .authors import AuthorIndex
from .chat import TOKEN_RE
from .graph import build_graph, build_impact_summary, paper_detail, ref_order
from .llm import LLM, QuotaExhaustedError
from .question import MAX_QUESTION_LEN, normalize_question  # noqa: F401  (re-exported)

log = logging.getLogger(__name__)

FAILED_PAIR = "The comparison could not be completed (model error), so no judgment is shown."
FAILED_IMPACT = "The impact assessment could not be completed (model error), so no judgment is shown."


class NoQuestionError(RuntimeError):
    """No active research question (none set via the workspace and no .env default)."""


class NoLibraryError(RuntimeError):
    """Evaluation requested before any library paper with claims exists."""


class DuplicatePaperError(RuntimeError):
    """The same PDF (same content hash) is already stored; re-uploading would replace its analysis."""

    def __init__(self, paper: Dict):
        super().__init__(f'This PDF is already in the evidence graph as "{paper["title"]}". It was not uploaded '
                         "again, because that would replace its existing claims and relationships.")
        self.paper = paper


class ModelUnavailableError(RuntimeError):
    """Gemini failed during extraction/embedding of an upload; the upload was rolled back."""


class QuestionConflict(RuntimeError):
    """Changing the question would mix analyses; requires an explicit, user-confirmed choice."""

    def __init__(self, current: Optional[str], counts: Dict):
        super().__init__("Existing analysis was produced under a different research question and is not "
                         "reinterpreted. Resend with keep_papers=true to keep the papers and clear their "
                         "analysis for re-analysis, or reset=true to delete everything.")
        self.current, self.counts = current, counts


class PendingAnalysisError(RuntimeError):
    """Papers are waiting to be re-analyzed under the current question; finish that first."""


class AnalysisOrderError(RuntimeError):
    """An evaluated paper's impact needs the re-analyzed library; analyze library papers first."""


class AlreadyAnalyzedError(RuntimeError):
    """The paper already has its analysis under the current question."""


# ---------- workspace: the single active research question ----------
def current_question() -> Optional[str]:
    """The current session's confirmed question, or None. (Stage D: the optional .env RESEARCH_QUESTION is never
    applied to a session implicitly — it is only a default for the seed CLI, which stores it explicitly.)"""
    with db.connect() as conn:
        return db.get_question(conn)


def active_question() -> str:
    q = current_question()
    if not q:
        raise NoQuestionError("Set a research question first (PUT /api/workspace).")
    return q


def workspace_state() -> Dict:
    with db.connect() as conn:
        stored = db.get_question(conn)
        counts = db.analysis_counts(conn)
        pending = db.pending_papers(conn)
    return {"question": stored,
            "source": "workspace" if stored else None,
            "is_default": False,
            "counts": counts,
            "has_library": counts["library_claims"] > 0,
            "pending_papers": [{"id": p["id"], "title": p["title"], "is_new": bool(p["is_new"]),
                                "status": p["analysis_status"]} for p in pending]}


def set_workspace_question(question: str, reset: bool = False, keep_papers: bool = False) -> Dict:
    """Set the active research question. Only called after the user confirmed the question in the UI.

    - Same question as the current one: no-op for the analysis (never deletes, whatever the flags).
    - Different question, no stored papers: just stores it.
    - Different question, stored papers: raises QuestionConflict (nothing changes) unless the caller made an
      explicit choice, applied with the new question in ONE transaction (all-or-nothing):
        keep_papers=True  keep papers + sentences + node numbers + chat; clear claims/relationships/impacts;
                          papers become 'pending' and are re-analyzed only when the user asks
                          (POST /api/papers/{id}/analyze). Results are never reinterpreted silently.
        reset=True        delete everything (papers included) and start a new evidence graph.
    """
    q = normalize_question(question)
    if reset and keep_papers:
        raise ValueError("Choose either keep_papers or reset, not both.")
    did_reset, cleared = False, 0
    with db.connect() as conn:  # commits on success; any error rolls back the delete and the update
        stored = db.get_question(conn)
        current = stored
        counts = db.analysis_counts(conn)
        changed = q != current
        if changed and counts["papers"] > 0:
            if keep_papers or reset:
                db.lock_epochs(conn)  # in-flight uploads/answers based on the old analysis must not write back
                db.bump_epochs(conn, papers=True, chat=reset)
            if keep_papers:
                cleared = db.clear_analysis(conn)
            elif reset:
                db.delete_all(conn)
                did_reset = True
            else:
                raise QuestionConflict(current, counts)
        db.set_question(conn, q)
    return {**workspace_state(), "reset": did_reset, "analysis_cleared": cleared > 0, "previous_question": current,
            "changed": changed}


def _parallel(fn: Callable, items: Sequence) -> List:
    """Run fn over items concurrently; each result is the return value or the raised Exception."""
    def safe(item):
        try:
            return fn(item)
        except Exception as exc:  # one failed Gemini call must not sink the whole run
            log.warning("LLM call failed: %s", exc)
            return exc
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, settings.llm_workers)) as ex:
        return list(ex.map(safe, items))


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_paper_id(title: str, data: bytes) -> str:
    return f"{ingest.slugify(title, 30)}-{hashlib.sha1(data).hexdigest()[:6]}"


# ---------- 1. ingest ----------
def add_paper(*, paper_id: str, title: str, pdf_bytes: Optional[bytes] = None, abstract_text: Optional[str] = None,
              doi: Optional[str] = None, url: Optional[str] = None, year: Optional[int] = None,
              license: Optional[str] = None, access_level: Optional[str] = None, is_new: bool = False,
              analysis_status: str = "complete") -> Dict:
    """Store a paper and its sentence index (replacing any previous version with the same id)."""
    ingest.check_paper_id(paper_id)
    version = None
    if pdf_bytes:
        # Block-aware extraction for new uploads (stored sentences of earlier papers are never re-extracted).
        pages, level, version = ingest.extract_page_blocks(pdf_bytes), access_level or "full", ingest.INGEST_VERSION
    elif abstract_text and abstract_text.strip():
        pages, level = [abstract_text], "abstract"
    else:
        raise ValueError("Provide a PDF or an abstract.")
    sentences = ingest.build_sentences(paper_id, pages)  # raises NoExtractableTextError if empty
    with db.connect() as conn:
        db.delete_paper(conn, paper_id)
        db.insert_paper(conn, {"id": paper_id, "title": title, "doi": doi, "url": url, "year": year,
                               "license": license, "access_level": level, "is_new": is_new,
                               "content_sha256": content_sha256(pdf_bytes) if pdf_bytes else None,
                               "analysis_status": analysis_status, "ingest_version": version})
        db.insert_sentences(conn, sentences)
    return {"paper_id": paper_id, "sentences": len(sentences), "access_level": level}


# ---------- 2. extract + embed ----------
_question_vecs: Dict[str, np.ndarray] = {}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def question_vector(llm: LLM, question: str) -> np.ndarray:
    if question not in _question_vecs:
        _question_vecs[question] = _unit(llm.embed([question])[0])
    return _question_vecs[question]


def extract_claims(llm: LLM, paper_id: str, question: Optional[str] = None) -> Tuple[List[int], Dict]:
    question = question or active_question()
    with db.connect() as conn:
        paper = db.get_paper(conn, paper_id)
        sentences = db.paper_sentences(conn, paper_id)
    prompt = build_extraction_prompt(question, paper["title"], sentences, settings.max_claims,
                                     settings.max_prompt_sentences)
    raw: ExtractionResult = llm.generate_json(prompt, ExtractionResult)
    cited = [sid for c in raw.claims for sid in (c.sentence_ids or [])]
    with db.connect() as conn:
        stored = db.stored_paper_map(conn, [str(s).strip().strip("[]") for s in cited if s])
    kept, report = validate_claims(raw, paper_id, stored, settings.max_claims)
    vectors = llm.embed([c["text"] for c in kept]) if kept else []
    qv = question_vector(llm, question) if kept else None
    with db.connect() as conn:
        ids = [db.insert_claim(conn, c, v) for c, v in zip(kept, vectors)]
        relevance = max(float(np.dot(_unit(v), qv)) for v in vectors) if vectors else None
        db.set_relevance(conn, paper_id, relevance)
        db.set_extraction_report(conn, paper_id, {**report, "question": question})
    log.info("extracted %s: %s", paper_id, report)
    return ids, report


def _claim_inputs(conn, claim_ids) -> Dict[int, Dict]:
    claims = db.claims_by_ids(conn, claim_ids)
    for c in claims.values():
        c["context"] = db.context_sentences(conn, c["sentence_ids"], window=1)
    return claims


# ---------- 3. compare ----------
def compare_claims(llm: LLM, claim_ids: List[int], question: Optional[str] = None, refresh: bool = False) -> List[int]:
    """Top-k match `claim_ids` against claims from other papers and judge each pair with Gemini."""
    question = question or active_question()
    with db.connect() as conn:
        pairs = dedupe_pairs(db.candidate_pairs(conn, claim_ids, settings.top_k), settings.sim_threshold)
        if not refresh:
            existing = db.existing_pairs(conn)
            pairs = [p for p in pairs if (p["a"], p["b"]) not in existing]
        info = _claim_inputs(conn, {i for p in pairs for i in (p["a"], p["b"])})
        authors = author_index(conn, db.all_papers(conn)) if pairs else None
    jobs = []
    for p in pairs:
        a, b = info[p["a"]], info[p["b"]]
        diff = condition_diff(a["conditions"], b["conditions"])
        dependence = authors.pair(a["paper_id"], b["paper_id"])["summary"]
        jobs.append((p, a, b, diff, build_pair_prompt(question, a, b, diff, dependence)))
    results = _parallel(lambda job: llm.generate_json(job[4], PairJudgment), jobs)
    cited = [s for r in results if isinstance(r, PairJudgment) for s in r.cited_sentence_ids if s]
    rel_ids = []
    with db.connect() as conn:
        stored = db.stored_paper_map(conn, [str(s).strip().strip("[]") for s in cited])
        for (p, a, b, diff, _), res in zip(jobs, results):
            if isinstance(res, PairJudgment):
                verdict = apply_pair_guardrail(res, a["paper_id"], b["paper_id"], stored)
            else:
                verdict = {"label": "unresolved", "explanation": FAILED_PAIR, "key_differences": [],
                           "cited_sentence_ids": [], "insufficient_evidence": True}
            rel_ids.append(db.upsert_relationship(conn, {
                "claim_a": p["a"], "claim_b": p["b"], "similarity": p["sim"], "condition_diff": diff,
                "model": getattr(llm, "model_name", None), **verdict}))
    return rel_ids


# ---------- 4. new-paper impact (separate Gemini judgment; never mapped from labels) ----------
def assess_impact(llm: LLM, new_paper_id: str, question: Optional[str] = None) -> int:
    question = question or active_question()
    with db.connect() as conn:
        new_ids = set(db.claim_ids_for_paper(conn, new_paper_id))
        rels = db.relationships_touching(conn, list(new_ids))
        info = _claim_inputs(conn, {i for r in rels for i in (r["claim_a"], r["claim_b"])})
        authors = author_index(conn, db.all_papers(conn)) if rels else None
    jobs = []
    for r in rels:
        new_id = r["claim_a"] if r["claim_a"] in new_ids else r["claim_b"]
        prior_id = r["claim_b"] if new_id == r["claim_a"] else r["claim_a"]
        prior, new = info[prior_id], info[new_id]
        if prior["paper_id"] == new_paper_id:
            continue
        diff = condition_diff(prior["conditions"], new["conditions"])  # oriented earlier -> new
        rel_ctx = {"label": r["label"], "explanation": r["explanation"]} if not r["insufficient_evidence"] else {
            "label": "none (not verified)", "explanation": ""}
        dependence = authors.pair(prior["paper_id"], new["paper_id"])["summary"]
        jobs.append((r, prior, new, build_impact_prompt(question, prior, new, diff, rel_ctx, dependence)))
    results = _parallel(lambda job: llm.generate_json(job[3], ImpactJudgment), jobs)
    cited = [s for r in results if isinstance(r, ImpactJudgment) for s in r.cited_sentence_ids if s]
    with db.connect() as conn:
        stored = db.stored_paper_map(conn, [str(s).strip().strip("[]") for s in cited])
        for (r, prior, new, _), res in zip(jobs, results):
            if isinstance(res, ImpactJudgment):
                verdict = apply_impact_guardrail(res, prior["paper_id"], new["paper_id"], stored)
            else:
                verdict = {"impact": "leaves_unresolved", "rationale": FAILED_IMPACT, "cited_sentence_ids": [],
                           "insufficient_evidence": True}
            db.upsert_impact(conn, {"new_claim_id": new["id"], "prior_claim_id": prior["id"],
                                    "relationship_id": r["id"], "model": getattr(llm, "model_name", None), **verdict})
    return len(jobs)


# ---------- read models ----------
def _sentences_for(conn, claims, rels=(), impacts=()) -> Dict[str, Dict]:
    ids = [s for c in claims for s in c["sentence_ids"]]
    ids += [s for r in rels for s in (r.get("cited_sentence_ids") or [])]
    ids += [s for i in impacts for s in (i.get("cited_sentence_ids") or [])]
    return db.sentences_by_ids(conn, ids)


def author_index(conn, papers: List[Dict]) -> AuthorIndex:
    """Author lists for this request, parsed from stored front-matter sentences (nothing is stored)."""
    return AuthorIndex(papers, db.page1_sentences(conn, [p["id"] for p in papers]))


def graph_payload(question: Optional[str] = None) -> Dict:
    question = question or current_question()  # may be None before a question is set
    with db.connect() as conn:
        papers, claims, rels = db.all_papers(conn), db.all_claims(conn), db.all_relationships(conn)
        sentences = _sentences_for(conn, claims, rels)
        latest = db.latest_new_paper(conn)
        disc_row = db.latest_discovery_for(conn, latest["id"]) if latest else None
        refs = db.assign_node_refs(conn, ref_order(papers, claims))
        authors, reviews = author_index(conn, papers), db.all_reviews(conn)
    discovery = dict(disc_row["results"], for_paper_id=latest["id"]) if disc_row else None
    return build_graph(question, papers, claims, rels, sentences, discovery=discovery, refs=refs,
                       authors=authors, reviews=reviews)


def impact_summary(paper_id: str, extraction: Optional[Dict] = None, status: str = "live") -> Dict:
    with db.connect() as conn:
        paper = db.get_paper(conn, paper_id)
        if not paper:
            raise KeyError(paper_id)
        papers, claims = db.all_papers(conn), db.all_claims(conn)
        impacts = db.impacts_for_paper(conn, paper_id)
        sentences = _sentences_for(conn, claims, impacts=impacts)
        authors, reviews = author_index(conn, papers), db.all_reviews(conn)
    return build_impact_summary(paper, claims, impacts, sentences, papers, extraction=extraction, status=status,
                                authors=authors, reviews=reviews)


def extraction_warning(report: Dict) -> Optional[str]:
    """Plain statement of why a paper produced no claims (never claims more than the run shows)."""
    if report.get("outcome") == "no_relevant_findings":
        return "No relevant findings identified for this question: Gemini returned no candidate findings."
    if report.get("outcome") == "all_candidates_rejected":
        return (f"{report.get('returned', 0)} candidate findings were returned, but none passed citation "
                "verification, so none were added.")
    return None


def _extract_or_discard(llm: LLM, paper_id: str, question: str) -> Tuple[List[int], Dict]:
    """Extraction + embedding for an upload. If Gemini fails here, delete the just-ingested paper
    (cascades to its sentences/claims) so no half-processed paper is left behind."""
    try:
        return extract_claims(llm, paper_id, question)
    except db.StaleOperation:
        raise  # the project was cleared meanwhile: the paper is already gone, nothing to roll back
    except Exception as exc:
        with db.connect() as conn:
            db.delete_paper(conn, paper_id)
        reason = str(exc) if isinstance(exc, QuotaExhaustedError) else f"{type(exc).__name__}"
        raise ModelUnavailableError(
            f"Gemini was unavailable during claim extraction ({reason}). The paper was not saved; "
            "please try again.") from exc


def _check_upload(pdf_bytes: bytes, question: Optional[str], replace: bool = False) -> str:
    """Fail before storing anything: no question, a PDF that is already stored (unless the caller
    explicitly asked to replace it — only the seed CLI does), or no extractable text."""
    question = question or active_question()
    with db.connect() as conn:
        if db.analysis_counts(conn)["pending_papers"]:
            raise PendingAnalysisError("Some papers are waiting to be re-analyzed under the current research "
                                       "question. Re-analyze them before adding new papers.")
    if not replace:
        with db.connect() as conn:
            existing = db.paper_by_content(conn, content_sha256(pdf_bytes), hashlib.sha1(pdf_bytes).hexdigest()[:6])
        if existing:
            raise DuplicatePaperError(existing)
    ingest.extract_pages(pdf_bytes)  # raises NoExtractableTextError
    return question


def add_library_paper(llm: LLM, pdf_bytes: bytes, title: Optional[str] = None, doi: Optional[str] = None,
                      url: Optional[str] = None, year: Optional[int] = None, license: Optional[str] = None,
                      question: Optional[str] = None, replace: bool = False) -> Dict:
    """Initial-library upload: ingest, extract, and compare against the existing library. No impact analysis."""
    question = _check_upload(pdf_bytes, question, replace)
    timings, t0 = {}, time.time()
    title = (title or "").strip() or ingest.guess_title(pdf_bytes)
    paper_id = make_paper_id(title, pdf_bytes)
    info = add_paper(paper_id=paper_id, title=title, pdf_bytes=pdf_bytes, doi=doi, url=url, year=year,
                     license=license, is_new=False, analysis_status="processing")
    timings["ingest"] = round(time.time() - t0, 1)
    claim_ids, report = _extract_or_discard(llm, paper_id, question)
    timings["extract"] = round(time.time() - t0, 1)
    rel_ids = compare_claims(llm, claim_ids, question)
    timings["compare"] = round(time.time() - t0, 1)
    with db.connect() as conn:
        db.set_analysis_status(conn, paper_id, "complete")
        paper = db.get_paper(conn, paper_id)
        counts = db.analysis_counts(conn)
    return {"mode": "library", "question": question, "paper": paper_detail(paper),
            "sentences": info["sentences"], "claims_kept": len(claim_ids), "extraction": report,
            "relationships_created": len(rel_ids), "library_papers": counts["library_papers"],
            "warning": None if claim_ids else extraction_warning(report),
            "timings_s": timings}


def process_new_paper(llm: LLM, pdf_bytes: bytes, title: Optional[str] = None, doi: Optional[str] = None,
                      url: Optional[str] = None, year: Optional[int] = None, license: Optional[str] = None,
                      question: Optional[str] = None, replace: bool = False) -> Dict:
    """The live demo step: evaluate a new paper against the existing claim graph.
    A PDF that is already stored is rejected unless `replace=True` (explicit re-computation)."""
    question = _check_upload(pdf_bytes, question, replace)
    with db.connect() as conn:
        if db.analysis_counts(conn)["library_claims"] == 0:
            raise NoLibraryError("There is no evidence library to evaluate against yet. "
                                 "Upload 3-5 library papers (mode=library) first.")
    timings, t0 = {}, time.time()
    title = (title or "").strip() or ingest.guess_title(pdf_bytes)
    paper_id = make_paper_id(title, pdf_bytes)
    add_paper(paper_id=paper_id, title=title, pdf_bytes=pdf_bytes, doi=doi, url=url, year=year,
              license=license, is_new=True, analysis_status="processing")
    timings["ingest"] = round(time.time() - t0, 1)
    claim_ids, report = _extract_or_discard(llm, paper_id, question)
    timings["extract"] = round(time.time() - t0, 1)
    compare_claims(llm, claim_ids, question)
    timings["compare"] = round(time.time() - t0, 1)
    assess_impact(llm, paper_id, question)
    timings["impact"] = round(time.time() - t0, 1)
    with db.connect() as conn:
        db.set_analysis_status(conn, paper_id, "complete")
    summary = impact_summary(paper_id, extraction=report)
    summary["timings_s"] = timings
    summary["mode"] = "evaluate"
    summary["question"] = question
    return summary


def analyze_stored_paper(llm: LLM, paper_id: str, question: Optional[str] = None) -> Dict:
    """Deliberate re-analysis (user-triggered) of a stored paper under the current question: extraction from
    its stored sentences, comparison with already-analyzed claims, and impact for evaluated papers.
    The paper and its sentences are never deleted; if Gemini fails it simply returns to 'pending'."""
    question = question or active_question()
    with db.connect() as conn:
        paper = db.get_paper(conn, paper_id)
        if not paper:
            raise KeyError(paper_id)
        if paper["analysis_status"] == "complete":
            raise AlreadyAnalyzedError(f'"{paper["title"]}" is already analyzed under the current question.')
        if paper["is_new"]:
            if any(not p["is_new"] for p in db.pending_papers(conn)):
                raise AnalysisOrderError("Re-analyze the library papers first: an evaluated paper's impact is "
                                         "assessed against the library.")
            if db.analysis_counts(conn)["library_claims"] == 0:
                raise NoLibraryError("There is no analyzed library to evaluate this paper against.")
        db.delete_claims_for_paper(conn, paper_id)  # leftovers of an interrupted run
        db.set_analysis_status(conn, paper_id, "processing")
    t0 = time.time()
    try:
        claim_ids, report = extract_claims(llm, paper_id, question)
    except db.StaleOperation:
        raise
    except Exception as exc:
        with db.connect() as conn:
            db.delete_claims_for_paper(conn, paper_id)
            db.set_analysis_status(conn, paper_id, "pending")
        reason = str(exc) if isinstance(exc, QuotaExhaustedError) else type(exc).__name__
        raise ModelUnavailableError(f"Gemini was unavailable during claim extraction ({reason}). The paper is "
                                    "kept and still awaits re-analysis; please try again.") from exc
    rel_ids = compare_claims(llm, claim_ids, question)
    impacts = assess_impact(llm, paper_id, question) if paper["is_new"] else 0
    with db.connect() as conn:
        db.set_analysis_status(conn, paper_id, "complete")
        paper = db.get_paper(conn, paper_id)
        remaining = len(db.pending_papers(conn))
    return {"paper": paper_detail(paper), "question": question, "claims_kept": len(claim_ids), "extraction": report,
            "relationships_created": len(rel_ids), "impacts_assessed": impacts, "pending_remaining": remaining,
            "timings_s": {"total": round(time.time() - t0, 1)}}


# ---------- clearing (user-confirmed; one project only) ----------
class AnalysisRunningError(RuntimeError):
    """Papers are being analyzed right now; clearing must wait until that finishes."""


def _refuse_while_processing(conn) -> None:
    running = db.processing_papers(conn)
    if running:
        names = ", ".join(f'"{p["title"]}"' for p in running[:3])
        raise AnalysisRunningError(f"{names} {'is' if len(running) == 1 else 'are'} being analyzed right now. "
                                   "Wait until the analysis finishes, then try again.")


def clear_chat() -> Dict:
    """Delete this project's conversation. Answers still being prepared are discarded (chat epoch)."""
    with db.connect() as conn:
        db.lock_epochs(conn)
        deleted = db.clear_chat(conn)
        db.bump_epochs(conn, chat=True)
    return {"deleted": deleted}


def clear_papers() -> Dict:
    """Delete every paper of this project and everything derived from it, including the conversation (its
    citations point at the deleted text). The research question is kept. Refused while an analysis runs."""
    with db.connect() as conn:
        db.lock_epochs(conn)  # waits for in-flight guarded writes; later ones see the new epoch and abort
        _refuse_while_processing(conn)
        counts = db.analysis_counts(conn)
        messages = conn.execute("SELECT count(*) AS n FROM chat_messages").fetchone()["n"]
        db.delete_all(conn)
        db.bump_epochs(conn, papers=True, chat=True)
    return {"deleted": {"papers": counts["papers"], "claims": counts["claims"],
                        "relationships": counts["relationships"], "chat_messages": messages},
            "workspace": workspace_state()}


def turns_touching(rows: List[Dict], fp: Dict, paper_id: str) -> List[int]:
    """Ids of chat messages that used evidence from the paper: cited one of its sentences, were asked about it
    (context), or reference its node / claims / relationships. A turn (user question + its answer) is flagged
    as a whole, so neither half is used as model context again."""
    sentences, claims, rels = set(fp["sentences"]), set(fp["claims"]), set(fp["relationships"])
    tokens = {f"paper:{paper_id}"} | {f"claim:{c}" for c in claims} | {f"rel:{r}" for r in rels}

    def touches(m: Dict) -> bool:
        if sentences.intersection(m.get("cited_sentence_ids") or []):
            return True
        ctx = m.get("context") or {}
        if (ctx.get("kind") == "paper" and ctx.get("id") == paper_id) or \
                (ctx.get("kind") == "claim" and ctx.get("id") in claims) or \
                (ctx.get("kind") == "relationship" and ctx.get("id") in rels):
            return True
        return bool(tokens.intersection(TOKEN_RE.findall(m.get("content") or "")))

    flagged, last_user = set(), None
    for m in rows:  # oldest first
        if m["role"] == "user":
            last_user = m
            if touches(m):
                flagged.add(m["id"])
        else:
            if touches(m) or (last_user is not None and last_user["id"] in flagged):
                flagged.add(m["id"])
                if last_user is not None:
                    flagged.add(last_user["id"])
            last_user = None
    return sorted(flagged)


def remove_paper(paper_id: str) -> Dict:
    """Delete one paper with its sentences, claims, relationships, impacts, reviews and discovery links.
    The conversation is kept, but turns that used the paper's evidence are flagged and never reach the model
    again. Node numbers are append-only and are not reused. Refused while an analysis runs."""
    with db.connect() as conn:
        db.lock_epochs(conn)
        paper = db.get_paper(conn, paper_id)
        if not paper:
            raise KeyError(paper_id)
        _refuse_while_processing(conn)
        fp = db.paper_footprint(conn, paper_id)
        rows = db.chat_messages(conn, "", limit=100000)
        flagged = db.flag_chat_messages(conn, turns_touching(rows, fp, paper_id), paper)
        db.delete_reviews_for(conn, fp["relationships"], fp["impacts"])
        db.delete_paper(conn, paper_id)
        db.bump_epochs(conn, papers=True)
    return {"paper_id": paper_id, "title": paper["title"], "was_evaluated": bool(paper["is_new"]),
            "deleted": {"claims": len(fp["claims"]), "relationships": len(fp["relationships"]),
                        "impacts": len(fp["impacts"])},
            "chat_messages_flagged": flagged, "workspace": workspace_state()}


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def lookup_upload(sha256: str) -> Dict:
    """Has this exact PDF (by full SHA-256) been stored, and has its analysis finished?
    Used by the UI to reconcile an upload whose HTTP request timed out or lost its connection."""
    digest = (sha256 or "").strip().lower()
    if not SHA256_RE.match(digest):
        raise ValueError("sha256 must be 64 hex characters")
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM papers WHERE content_sha256 = %s LIMIT 1", (digest,)).fetchone()
    if not row:
        return {"found": False, "status": None, "paper": None}
    return {"found": True, "status": row["analysis_status"], "paper": paper_detail(row)}
