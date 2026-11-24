"""Seed the preloaded library, or evaluate a new paper from the command line.

  .venv/bin/python seed.py --init                  # apply schema.sql (idempotent)
  .venv/bin/python seed.py                         # add manifest papers NOT yet in the DB, then compare
  .venv/bin/python seed.py --refresh               # re-judge existing pairs in place (after prompt changes)
  .venv/bin/python seed.py --reextract paper-a     # rebuild one paper's sentences/claims (and its pairs)
  .venv/bin/python seed.py --reset --yes           # wipe EVERYTHING (incl. uploaded papers), then seed
  .venv/bin/python seed.py --new data/papers/d.pdf --title "Paper D title"   # precompute Paper D

Re-running without flags never deletes data: existing papers are skipped and only new pairs are judged.
Exit code is nonzero if citation-integrity checks fail.

The research question comes from --question, else the manifest's "question", else the question already
set in the app (workspace), else RESEARCH_QUESTION in .env. A different question than the one the
existing analysis was built with is refused unless --reset --yes is given.

Manifest format (data/papers/manifest.json):
  {"question": "Do AI tutors improve learning?",
   "papers": [{"id": "paper-a", "file": "a.pdf", "title": "...", "doi": "10....", "year": 2023,
               "url": "https://...", "license": "cc-by"},
              {"id": "paper-b", "abstract": "Abstract text...", "title": "...", "doi": "..."}]}
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from app import db, pipeline
from app.config import ROOT, settings
from app.llm import LLM, get_llm

MANIFEST = ROOT / "data" / "papers" / "manifest.json"

def resolve_question(question: Optional[str], reset: bool, out=print) -> str:
    """Apply --question / manifest question with the same protection as PUT /api/workspace."""
    if reset:
        with db.connect() as conn:  # one transaction: wipe + set question
            db.delete_all(conn)
            if question:
                db.set_question(conn, pipeline.normalize_question(question))
        out("reset: all papers, claims, relationships and impacts deleted")
    elif question:
        try:
            pipeline.set_workspace_question(question)
        except pipeline.QuestionConflict as exc:
            raise SystemExit(f"The database holds analysis for a different question ({exc.current!r}). "
                             "Use --reset --yes to start over with the new question.")
    try:
        return pipeline.active_question()
    except pipeline.NoQuestionError:
        raise SystemExit("No research question: pass --question, add \"question\" to the manifest, "
                         "or set it in the app.")

def seed(llm: LLM, manifest_path: Path = MANIFEST, reset: bool = False, refresh: bool = False,
         reextract: Iterable[str] = (), out=print, question: Optional[str] = None) -> dict:
    db.init_schema()
    manifest = json.loads(Path(manifest_path).read_text())
    # The .env RESEARCH_QUESTION is only a CLI default; it is stored explicitly in the session, never implied.
    question = resolve_question(question or manifest.get("question") or settings.research_question, reset, out)
    out(f"research question: {question}")
    reextract = set(reextract)
    unknown = reextract - {p["id"] for p in manifest["papers"]}
    if unknown:
        raise SystemExit(f"--reextract ids not in manifest: {sorted(unknown)}")
    with db.connect() as conn:
        papers = db.all_papers(conn)
    existing = {p["id"] for p in papers}
    uploads = [p["id"] for p in papers if p["is_new"]]
    stats = {"added": [], "skipped": [], "reextracted": []}
    for p in manifest["papers"]:
        if p["id"] in existing and p["id"] not in reextract:
            stats["skipped"].append(p["id"])
            out(f"{p['id']}: already in database, skipped")
            continue
        t = time.time()
        kwargs = {k: p.get(k) for k in ("doi", "url", "year", "license")}
        if p.get("file"):
            info = pipeline.add_paper(paper_id=p["id"], title=p["title"],
                                      pdf_bytes=(Path(manifest_path).parent / p["file"]).read_bytes(), **kwargs)
        else:
            info = pipeline.add_paper(paper_id=p["id"], title=p["title"], abstract_text=p["abstract"], **kwargs)
        _, report = pipeline.extract_claims(llm, p["id"], question)
        (stats["reextracted"] if p["id"] in existing else stats["added"]).append(p["id"])
        out(f"{p['id']}: {info['sentences']} sentences ({info['access_level']}), {report['kept']} claims kept, "
            f"rejected: {report['rejected_invalid_citations']} invalid citations, "
            f"{report['rejected_empty_text']} empty, {report['rejected_over_limit']} over limit "
            f"[{time.time() - t:.0f}s]")
    if stats["reextracted"] and uploads:
        out(f"WARNING: re-extracting removed impacts of uploaded papers {uploads} on "
            f"{stats['reextracted']}; re-run `seed.py --new ...` for those uploads.")
    t = time.time()
    with db.connect() as conn:
        all_ids = [c["id"] for c in db.all_claims(conn)]
    stats["compared"] = len(pipeline.compare_claims(llm, all_ids, question, refresh=refresh))
    out(f"judged {stats['compared']} claim pairs{' (refresh)' if refresh else ''} "
        f"(top_k={settings.top_k}, threshold={settings.sim_threshold}) [{time.time() - t:.0f}s]")
    stats["violations"] = check(out)
    return stats

def evaluate_new(llm: LLM, path: str, title: Optional[str], out=print) -> dict:
    """Evaluate a new paper; returns integrity violations ({} when clean)."""
    # An explicit operator command: re-running it intentionally recomputes this paper's analysis.
    summary = pipeline.process_new_paper(llm, Path(path).read_bytes(), title=title, replace=True)
    out(json.dumps({"paper": summary["paper"]["id"], "counts": summary["counts"],
                    "timings_s": summary["timings_s"], "extraction": summary["extraction"]}, indent=1))
    for i in summary["impacts"]:
        flag = " [insufficient evidence]" if i["insufficient_evidence"] else ""
        out(f"- {i['impact'].upper()}{flag}: {i['prior_claim']['text'][:90]}")
    return check(out)

def check(out=print) -> dict:
    with db.connect() as conn:
        bad = {k: ids for k, ids in db.integrity_violations(conn).items() if ids}
    out("integrity checks: OK" if not bad else f"integrity checks FAILED: {bad}")
    return bad

def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="only apply schema.sql")
    ap.add_argument("--reset", action="store_true", help="delete ALL data first (requires --yes)")
    ap.add_argument("--yes", action="store_true", help="confirm --reset")
    ap.add_argument("--refresh", action="store_true", help="re-judge pairs that already have a relationship")
    ap.add_argument("--reextract", action="append", default=[], metavar="PAPER_ID",
                    help="rebuild this manifest paper (repeatable)")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--question", help="research question (overrides the manifest's \"question\")")
    ap.add_argument("--new", help="path to a new paper PDF to evaluate")
    ap.add_argument("--title", help="title for --new")
    ap.add_argument("--session", help="research session id to operate on (see GET /api/sessions)")
    ap.add_argument("--new-session", metavar="TITLE", help="create a new research session and operate on it")
    args = ap.parse_args(argv)
    if args.init:
        db.init_schema()
        print("schema applied")
        return 0
    if args.session and args.new_session:
        print("Pass either --session or --new-session, not both.")
        return 2
    if args.new_session:
        db.init_schema()
        sid = db.create_session(args.new_session)["id"]
        print(f"created research session {sid}")
    elif args.session:
        try:
            sid = db.get_session(args.session)["id"]
        except (ValueError, db.SessionNotFound):
            print(f"Unknown research session: {args.session!r}")
            return 2
    elif db.current_session():
        sid = db.current_session()  # an in-process caller already bound a session explicitly
    else:
        print("Choose a research session: --session ID (see GET /api/sessions) or --new-session TITLE.")
        return 2
    with db.use_session(sid):
        return _run(args)

def _run(args) -> int:
    if args.reset and not args.yes:
        print("--reset deletes ALL papers, claims, relationships, impacts and uploaded papers. "
              "Re-run with --reset --yes to confirm.")
        return 2
    if args.new:
        return 1 if evaluate_new(get_llm(), args.new, args.title) else 0
    stats = seed(get_llm(), Path(args.manifest), reset=args.reset, refresh=args.refresh, reextract=args.reextract,
                 question=args.question)
    return 1 if stats["violations"] else 0

if __name__ == "__main__":
    sys.exit(main())