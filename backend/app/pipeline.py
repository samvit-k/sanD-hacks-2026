"""End-to-end pipeline: ingest -> extract -> embed -> top-k match -> compare -> (new paper) impact.

Database work happens in short transactions; Gemini calls run in a small thread pool between them.
"""
import hashlib
import logging
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
from .graph import build_graph, build_impact_summary, paper_detail
from .llm import LLM, QuotaExhaustedError

log = logging.getLogger(__name__)

FAILED_PAIR = "The comparison could not be completed (model error), so no judgment is shown."
FAILED_IMPACT = "The impact assessment could not be completed (model error), so no judgment is shown."
MAX_QUESTION_LEN = 300


class NoQuestionError(RuntimeError):
    """No active research question (none set via the workspace and no .env default)."""


class NoLibraryError(RuntimeError):
    """Evaluation requested before any library paper with claims exists."""


class ModelUnavailableError(RuntimeError):
    """Gemini failed during extraction/embedding of an upload; the upload was rolled back."""


class QuestionConflict(RuntimeError):
    """Changing the question would mix analyses; requires an explicit reset."""

    def __init__(self, current: Optional[str], counts: Dict):
        super().__init__("Existing analysis was produced under a different research question. "
                         "Resend with reset=true to delete it and start a new evidence graph.")
        self.current, self.counts = current, counts


# ---------- workspace: the single active research question ----------
def normalize_question(question: Optional[str]) -> str:
    q = " ".join((question or "").split())
    if not q:
        raise ValueError("The research question must not be empty.")
    if len(q) > MAX_QUESTION_LEN:
        raise ValueError(f"The research question must be at most {MAX_QUESTION_LEN} characters.")
    return q


def current_question() -> Optional[str]:
    """Stored workspace question, else the optional .env default, else None."""
    with db.connect() as conn:
        stored = db.get_question(conn)
    return stored or settings.research_question


def active_question() -> str:
    q = current_question()
    if not q:
        raise NoQuestionError("Set a research question first (PUT /api/workspace).")
    return q


def workspace_state() -> Dict:
    with db.connect() as conn:
        stored = db.get_question(conn)
        counts = db.analysis_counts(conn)
    question = stored or settings.research_question
    return {"question": question,
            "source": "workspace" if stored else ("default" if question else None),
            "is_default": not stored and bool(question),
            "counts": counts,
            "has_library": counts["library_claims"] > 0}


def set_workspace_question(question: str, reset: bool = False) -> Dict:
    """Set the active research question.

    - Same question as the current one: no-op for the analysis (never deletes, even with reset=True).
    - Different question, no existing analysis: just stores it.
    - Different question, existing analysis: raises QuestionConflict unless reset=True, in which case the
      analysis is deleted and the new question stored in ONE transaction (all-or-nothing).
    """
    q = normalize_question(question)
    did_reset = False
    with db.connect() as conn:  # commits on success; any error rolls back the delete and the update
        stored = db.get_question(conn)
        current = stored or settings.research_question
        counts = db.analysis_counts(conn)
        changed = q != current
        if changed and counts["papers"] > 0:
            if not reset:
                raise QuestionConflict(current, counts)
            db.delete_all(conn)
            did_reset = True
        db.set_question(conn, q)
    return {**workspace_state(), "reset": did_reset}


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


def make_paper_id(title: str, data: bytes) -> str:
    return f"{ingest.slugify(title, 30)}-{hashlib.sha1(data).hexdigest()[:6]}"


# ---------- 1. ingest ----------
def add_paper(*, paper_id: str, title: str, pdf_bytes: Optional[bytes] = None, abstract_text: Optional[str] = None,
              doi: Optional[str] = None, url: Optional[str] = None, year: Optional[int] = None,
              license: Optional[str] = None, access_level: Optional[str] = None, is_new: bool = False) -> Dict:
    """Store a paper and its sentence index (replacing any previous version with the same id)."""
    ingest.check_paper_id(paper_id)
    if pdf_bytes:
        pages, level = ingest.extract_pages(pdf_bytes), access_level or "full"
    elif abstract_text and abstract_text.strip():
        pages, level = [abstract_text], "abstract"
    else:
        raise ValueError("Provide a PDF or an abstract.")
    sentences = ingest.build_sentences(paper_id, pages)  # raises NoExtractableTextError if empty
    with db.connect() as conn:
        db.delete_paper(conn, paper_id)
        db.insert_paper(conn, {"id": paper_id, "title": title, "doi": doi, "url": url, "year": year,
                               "license": license, "access_level": level, "is_new": is_new})
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
    jobs = []
    for p in pairs:
        a, b = info[p["a"]], info[p["b"]]
        diff = condition_diff(a["conditions"], b["conditions"])
        jobs.append((p, a, b, diff, build_pair_prompt(question, a, b, diff)))
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
        jobs.append((r, prior, new, build_impact_prompt(question, prior, new, diff, rel_ctx)))
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


def graph_payload(question: Optional[str] = None) -> Dict:
    question = question or current_question()  # may be None before a question is set
    with db.connect() as conn:
        papers, claims, rels = db.all_papers(conn), db.all_claims(conn), db.all_relationships(conn)
        sentences = _sentences_for(conn, claims, rels)
        latest = db.latest_new_paper(conn)
        disc_row = db.latest_discovery_for(conn, latest["id"]) if latest else None
    discovery = dict(disc_row["results"], for_paper_id=latest["id"]) if disc_row else None
    return build_graph(question, papers, claims, rels, sentences, discovery=discovery)


def impact_summary(paper_id: str, extraction: Optional[Dict] = None, status: str = "live") -> Dict:
    with db.connect() as conn:
        paper = db.get_paper(conn, paper_id)
        if not paper:
            raise KeyError(paper_id)
        papers, claims = db.all_papers(conn), db.all_claims(conn)
        impacts = db.impacts_for_paper(conn, paper_id)
        sentences = _sentences_for(conn, claims, impacts=impacts)
    return build_impact_summary(paper, claims, impacts, sentences, papers, extraction=extraction, status=status)


def _extract_or_discard(llm: LLM, paper_id: str, question: str) -> Tuple[List[int], Dict]:
    """Extraction + embedding for an upload. If Gemini fails here, delete the just-ingested paper
    (cascades to its sentences/claims) so no half-processed paper is left behind."""
    try:
        return extract_claims(llm, paper_id, question)
    except Exception as exc:
        with db.connect() as conn:
            db.delete_paper(conn, paper_id)
        reason = str(exc) if isinstance(exc, QuotaExhaustedError) else f"{type(exc).__name__}"
        raise ModelUnavailableError(
            f"Gemini was unavailable during claim extraction ({reason}). The paper was not saved; "
            "please try again.") from exc


def _check_upload(pdf_bytes: bytes, question: Optional[str]) -> str:
    """Fail before storing anything: no question, or no extractable text."""
    question = question or active_question()
    ingest.extract_pages(pdf_bytes)  # raises NoExtractableTextError
    return question


def add_library_paper(llm: LLM, pdf_bytes: bytes, title: Optional[str] = None, doi: Optional[str] = None,
                      url: Optional[str] = None, year: Optional[int] = None, license: Optional[str] = None,
                      question: Optional[str] = None) -> Dict:
    """Initial-library upload: ingest, extract, and compare against the existing library. No impact analysis."""
    question = _check_upload(pdf_bytes, question)
    timings, t0 = {}, time.time()
    title = (title or "").strip() or ingest.guess_title(pdf_bytes)
    paper_id = make_paper_id(title, pdf_bytes)
    info = add_paper(paper_id=paper_id, title=title, pdf_bytes=pdf_bytes, doi=doi, url=url, year=year,
                     license=license, is_new=False)
    timings["ingest"] = round(time.time() - t0, 1)
    claim_ids, report = _extract_or_discard(llm, paper_id, question)
    timings["extract"] = round(time.time() - t0, 1)
    rel_ids = compare_claims(llm, claim_ids, question)
    timings["compare"] = round(time.time() - t0, 1)
    with db.connect() as conn:
        paper = db.get_paper(conn, paper_id)
        counts = db.analysis_counts(conn)
    return {"mode": "library", "question": question, "paper": paper_detail(paper),
            "sentences": info["sentences"], "claims_kept": len(claim_ids), "extraction": report,
            "relationships_created": len(rel_ids), "library_papers": counts["library_papers"],
            "warning": None if claim_ids else "No verifiable claims were extracted from this paper.",
            "timings_s": timings}


def process_new_paper(llm: LLM, pdf_bytes: bytes, title: Optional[str] = None, doi: Optional[str] = None,
                      url: Optional[str] = None, year: Optional[int] = None, license: Optional[str] = None,
                      question: Optional[str] = None) -> Dict:
    """The live demo step: evaluate a new paper against the existing claim graph."""
    question = _check_upload(pdf_bytes, question)
    with db.connect() as conn:
        if db.analysis_counts(conn)["library_claims"] == 0:
            raise NoLibraryError("There is no evidence library to evaluate against yet. "
                                 "Upload 3-5 library papers (mode=library) first.")
    timings, t0 = {}, time.time()
    title = (title or "").strip() or ingest.guess_title(pdf_bytes)
    paper_id = make_paper_id(title, pdf_bytes)
    add_paper(paper_id=paper_id, title=title, pdf_bytes=pdf_bytes, doi=doi, url=url, year=year,
              license=license, is_new=True)
    timings["ingest"] = round(time.time() - t0, 1)
    claim_ids, report = _extract_or_discard(llm, paper_id, question)
    timings["extract"] = round(time.time() - t0, 1)
    compare_claims(llm, claim_ids, question)
    timings["compare"] = round(time.time() - t0, 1)
    assess_impact(llm, paper_id, question)
    timings["impact"] = round(time.time() - t0, 1)
    summary = impact_summary(paper_id, extraction=report)
    summary["timings_s"] = timings
    summary["mode"] = "evaluate"
    summary["question"] = question
    return summary
