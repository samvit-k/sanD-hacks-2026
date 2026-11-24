"""Find related research: Bright Data Google Scholar Scraper -> OpenAlex resolution -> cache.

Results are discovery LEADS (metadata/abstract only), never evidence. Public, permitted sources only.
Lookup order: Bright Data cache -> live Bright Data (if configured) -> OpenAlex cache -> live OpenAlex.

Bright Data's async dataset API:
  POST /datasets/v3/trigger?dataset_id=...  -> {"snapshot_id": ...}
  GET  /datasets/v3/progress/{snapshot_id}  -> {"status": "running" | "ready" | "failed", ...}
  GET  /datasets/v3/snapshot/{snapshot_id}?format=json -> [records]

VERIFY BEFORE THE DEMO: the Scholar scraper's dataset_id, its input field (BRIGHTDATA_SCHOLAR_INPUT)
and its output fields. Save one raw response to backend/fixtures/brightdata_scholar_sample.json
(`python -m app.discover --sample "query"`) and adjust SCHOLAR_FIELDS below to the real keys.
"""
import argparse
import hashlib
import json
import logging
import re
import time
from typing import Dict, List, Optional

import httpx
from rapidfuzz import fuzz

from . import db
from .config import BACKEND, settings

log = logging.getLogger(__name__)

BD_BASE = "https://api.brightdata.com/datasets/v3"
OA_BASE = "https://api.openalex.org"
HEADERS = {"User-Agent": "SpiderMonkey/0.1 (hackathon prototype)"}
SAMPLE_PATH = BACKEND / "fixtures" / "brightdata_scholar_sample.json"

# Candidate keys per field, in priority order. Replace with the verified keys from the saved sample.
SCHOLAR_FIELDS = {
    "title": ("title", "article_title", "name"),
    "url": ("url", "link", "article_url", "title_link"),
    "year": ("year", "publication_year", "date"),
    "authors": ("authors", "author", "publication_info"),
    "cited_by": ("cited_by", "cited_by_count", "citations"),
    "snippet": ("snippet", "description", "abstract"),
}
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>?#]+)", re.I)

class BrightDataError(RuntimeError):
    pass

def cache_key(source: str, query: str) -> str:
    return hashlib.sha256(f"{source}|{' '.join(query.lower().split())}".encode()).hexdigest()

# ---------- Bright Data ----------
def _bd_headers() -> Dict:
    return {**HEADERS, "Authorization": f"Bearer {settings.brightdata_token}"}

def bd_input(query: str) -> Dict:
    template = json.loads(settings.brightdata_input_template)
    return {k: (v.replace("{query}", query) if isinstance(v, str) else v) for k, v in template.items()}

def _bd_json(r: httpx.Response, what: str):
    if r.status_code >= 400:
        raise BrightDataError(f"{what} failed: HTTP {r.status_code} {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise BrightDataError(f"{what} returned non-JSON: {r.text[:200]}") from exc

def bd_trigger(client: httpx.Client, query: str) -> str:
    r = client.post(f"{BD_BASE}/trigger", headers=_bd_headers(), json=[bd_input(query)],
                    params={"dataset_id": settings.brightdata_dataset_id, "include_errors": "true"})
    snap = (_bd_json(r, "trigger") or {}).get("snapshot_id")
    if not snap:
        raise BrightDataError(f"trigger returned no snapshot_id: {r.text[:200]}")
    return snap

def bd_wait_and_download(client: httpx.Client, snapshot_id: str, timeout_s: float, poll_s: float = 3.0,
                         max_transient_errors: int = 2) -> List[Dict]:
    """Poll until ready, then download. Fails fast on HTTP errors instead of waiting out the timeout:
    a 4xx (bad token, unknown snapshot) raises immediately; 429/5xx are retried at most
    `max_transient_errors` times in a row. Network errors propagate as httpx.HTTPError."""
    deadline = time.time() + timeout_s
    transient = 0
    while True:
        r = client.get(f"{BD_BASE}/progress/{snapshot_id}", headers=_bd_headers())
        if r.status_code == 429 or r.status_code >= 500:
            transient += 1
            if transient > max_transient_errors:
                raise BrightDataError(f"progress check failed: HTTP {r.status_code} ({transient} consecutive errors)")
        elif r.status_code >= 400:
            raise BrightDataError(f"progress check failed: HTTP {r.status_code} {r.text[:200]}")
        else:
            transient = 0
            status = (_bd_json(r, "progress check") or {}).get("status")
            if status == "ready":
                break
            if status == "failed":
                raise BrightDataError(f"snapshot {snapshot_id} failed")
        if time.time() >= deadline:
            raise BrightDataError(f"snapshot {snapshot_id} not ready after {timeout_s:.0f}s")
        time.sleep(poll_s)
    r = client.get(f"{BD_BASE}/snapshot/{snapshot_id}", headers=_bd_headers(), params={"format": "json"})
    if r.status_code == 202:
        raise BrightDataError(f"snapshot {snapshot_id} reported ready but download is still building")
    data = _bd_json(r, "download")
    return data if isinstance(data, list) else [data]

def _first(rec: Dict, keys) -> Optional[object]:
    for k in keys:
        if rec.get(k) not in (None, "", []):
            return rec[k]
    return None

def parse_scholar_records(records: List[Dict]) -> List[Dict]:
    """Map raw Scholar records to {title, url, doi, year, authors, cited_by, snippet}.
    Some scrapers nest results (e.g. {"organic": [...]}) — flatten one level."""
    flat: List[Dict] = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("error"):
            continue
        nested = next((rec[k] for k in ("organic", "results", "articles") if isinstance(rec.get(k), list)), None)
        flat.extend(r for r in (nested if nested is not None else [rec]) if isinstance(r, dict))
    out = []
    for rec in flat:
        title = _first(rec, SCHOLAR_FIELDS["title"])
        if not title:
            continue
        url = _first(rec, SCHOLAR_FIELDS["url"])
        year = _first(rec, SCHOLAR_FIELDS["year"])
        year_m = re.search(r"(19|20)\d{2}", str(year or ""))
        authors = _first(rec, SCHOLAR_FIELDS["authors"])
        doi_m = _DOI.search(" ".join(str(v) for v in (url, rec.get("doi")) if v))
        out.append({
            "title": str(title).strip(),
            "url": url,
            "doi": doi_m.group(1).rstrip(".").lower() if doi_m else None,
            "year": int(year_m.group(0)) if year_m else None,
            "authors": authors if isinstance(authors, str) else (", ".join(map(str, authors)) if authors else None),
            "cited_by": _first(rec, SCHOLAR_FIELDS["cited_by"]),
            "snippet": _first(rec, SCHOLAR_FIELDS["snippet"]),
        })
    return out

# ---------- OpenAlex ----------
def reconstruct_abstract(inv: Optional[Dict]) -> Optional[str]:
    if not inv:
        return None
    pos = sorted((i, w) for w, idxs in inv.items() for i in idxs)
    return " ".join(w for _, w in pos)

def oa_to_result(w: Dict) -> Dict:
    loc = w.get("best_oa_location") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower() or None
    return {
        "openalex_id": w.get("id"), "title": w.get("title") or w.get("display_name"), "doi": doi,
        "year": w.get("publication_year"), "url": w.get("doi") or w.get("id"),
        "oa_url": loc.get("pdf_url") or loc.get("landing_page_url"),
        "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
        "license": loc.get("license"), "cited_by_count": w.get("cited_by_count"),
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "access_level": "abstract", "resolved": True,
    }

def oa_resolve(client: httpx.Client, item: Dict) -> Optional[Dict]:
    """Resolve a Scholar result to an OpenAlex work by DOI, else by close title match."""
    if item.get("doi"):
        r = client.get(f"{OA_BASE}/works/doi:{item['doi']}", headers=HEADERS)
        if r.status_code == 200:
            return oa_to_result(r.json())
    r = client.get(f"{OA_BASE}/works", headers=HEADERS,
                   params={"search": item["title"], "per-page": 3})
    if r.status_code != 200:
        return None
    for w in r.json().get("results", []):
        if fuzz.token_sort_ratio((w.get("title") or "").lower(), item["title"].lower()) >= 90:
            return oa_to_result(w)
    return None

def oa_search(client: httpx.Client, query: str, n: int) -> List[Dict]:
    r = client.get(f"{OA_BASE}/works", headers=HEADERS, params={"search": query, "per-page": n})
    r.raise_for_status()
    return [oa_to_result(w) for w in r.json().get("results", [])]

def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

def dedupe(results: List[Dict], exclude_dois: List[str], limit: int) -> List[Dict]:
    seen, out = set(), []
    excluded = {d.lower() for d in exclude_dois}
    for r in results:
        keys = {k for k in (r.get("openalex_id"), r.get("doi"), _norm_title(r.get("title"))) if k}
        if (r.get("doi") and r["doi"] in excluded) or keys & seen:
            continue
        seen |= keys
        out.append(r)
    return out[:limit]

# ---------- orchestration ----------
def _from_brightdata(client: httpx.Client, query: str, wait_s: float) -> List[Dict]:
    snap = bd_trigger(client, query)
    raw = bd_wait_and_download(client, snap, wait_s)
    items = parse_scholar_records(raw)
    if not items:
        raise BrightDataError("no parsable Scholar records (check SCHOLAR_FIELDS against the saved sample)")
    resolved = []
    for it in items[: settings.discovery_limit * 2]:
        try:
            oa = oa_resolve(client, it)
        except httpx.HTTPError:
            oa = None
        resolved.append({**oa, "scholar_url": it.get("url"), "scholar_cited_by": it.get("cited_by")} if oa else
                        {**it, "access_level": "metadata", "resolved": False})
    return resolved

def _finish(key: str, payload: Dict, cached: bool, for_paper_id: Optional[str], **extra) -> Dict:
    """Link the (possibly shared) cached results to the requesting paper and return them."""
    if for_paper_id:
        with db.connect() as conn:
            db.link_discovery(conn, for_paper_id, key)
    return {**payload, **extra, "cached": cached, "for_paper_id": for_paper_id}

def discover(query: str, for_paper_id: Optional[str] = None, wait_s: Optional[float] = None,
             client: Optional[httpx.Client] = None) -> Dict:
    query = " ".join(query.split())
    wait_s = settings.brightdata_live_wait if wait_s is None else wait_s
    bd_key, oa_key = cache_key("brightdata", query), cache_key("openalex", query)
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        with db.connect() as conn:
            if for_paper_id and not db.get_paper(conn, for_paper_id):
                raise ValueError(f"unknown paper_id {for_paper_id!r}")
            exclude = db.library_dois(conn)
            bd_row = db.cache_get(conn, bd_key)
            oa_row = db.cache_get(conn, oa_key) if not settings.brightdata_configured else None
        if bd_row:
            return _finish(bd_key, bd_row["results"], True, for_paper_id)
        if oa_row:
            return _finish(oa_key, oa_row["results"], True, for_paper_id, fallback_reason="Bright Data not configured")
        fallback_reason = None
        if settings.brightdata_configured:
            try:
                results = dedupe(_from_brightdata(client, query, wait_s), exclude, settings.discovery_limit)
                payload = {"query": query, "source": "brightdata", "results": results, "fallback_reason": None}
                with db.connect() as conn:
                    db.cache_put(conn, bd_key, query, "brightdata", payload)
                return _finish(bd_key, payload, False, for_paper_id)
            except (BrightDataError, httpx.HTTPError) as exc:
                fallback_reason = f"Bright Data unavailable: {exc}"
                log.warning(fallback_reason)
        else:
            fallback_reason = "Bright Data not configured"
        with db.connect() as conn:
            oa_row = db.cache_get(conn, oa_key)
        if oa_row:
            return _finish(oa_key, oa_row["results"], True, for_paper_id, fallback_reason=fallback_reason)
        results = dedupe(oa_search(client, query, settings.discovery_limit * 2), exclude, settings.discovery_limit)
        payload = {"query": query, "source": "openalex", "results": results, "fallback_reason": fallback_reason}
        with db.connect() as conn:
            db.cache_put(conn, oa_key, query, "openalex", payload)
        return _finish(oa_key, payload, False, for_paper_id)
    finally:
        if own:
            client.close()

def main() -> None:
    ap = argparse.ArgumentParser(description="Pre-warm the discovery cache, or save a raw Bright Data sample.")
    ap.add_argument("query")
    ap.add_argument("--paper-id", help="attach results to this paper's node in the graph")
    ap.add_argument("--wait", type=float, default=900, help="seconds to wait for the Bright Data snapshot")
    ap.add_argument("--sample", action="store_true", help=f"only save the raw Scholar response to {SAMPLE_PATH}")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.sample:
        if not settings.brightdata_configured:
            raise SystemExit("Set BRIGHTDATA_API_TOKEN and BRIGHTDATA_SCHOLAR_DATASET_ID in .env first.")
        with httpx.Client(timeout=30) as client:
            raw = bd_wait_and_download(client, bd_trigger(client, args.query), args.wait)
        SAMPLE_PATH.write_text(json.dumps(raw, indent=2))
        keys = sorted({k for r in raw if isinstance(r, dict) for k in r})
        print(f"Saved {len(raw)} records to {SAMPLE_PATH}\nTop-level keys: {keys}")
        print(f"Parsed with current SCHOLAR_FIELDS: {len(parse_scholar_records(raw))} results")
        return
    res = discover(args.query, for_paper_id=args.paper_id, wait_s=args.wait)
    print(json.dumps({k: res[k] for k in ("source", "cached", "fallback_reason")}, indent=1))
    for r in res["results"]:
        print(f"- {r.get('title')} ({r.get('year')}) {r.get('doi') or ''}")

if __name__ == "__main__":
    main()