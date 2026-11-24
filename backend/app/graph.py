"""Pure builders for the two frontend contracts: the graph payload and the impact summary.

Both take plain rows (dicts) so the live API, the fixture generator and tests share one code path.
Every quote is rebuilt from stored sentences; unverified IDs are never emitted.
"""
from typing import Dict, List, Optional

from .authors import AuthorIndex, independence_flag
from .contracts import IMPACTS
from .ingest import layout_artifacts

def quote(sid: str, s: Dict) -> Dict:
    """A stored sentence as shown to users. `text` is the stored source text (never edited); `kind`/`text_check`
    say how it was extracted (NULL before D5); `artifacts` are display-only layout warnings."""
    return {"id": sid, "paper_id": s["paper_id"], "page": s["page"], "section": s.get("section", "front"),
            "text": s["text"], "kind": s.get("block_kind"), "text_check": s.get("text_check"),
            "artifacts": layout_artifacts(s["text"])}

def _quotes(ids: List[str], sentences: Dict[str, Dict]) -> List[Dict]:
    return [quote(sid, sentences[sid]) for sid in ids if sid in sentences]

def _short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"

def paper_detail(p: Dict, authors: Optional[AuthorIndex] = None) -> Dict:
    out = {"id": p["id"], "title": p["title"], "doi": p.get("doi"), "url": p.get("url"), "year": p.get("year"),
           "access_level": p.get("access_level", "full"), "license": p.get("license"),
           "is_new": bool(p.get("is_new")), "relevance": p.get("relevance"),
           "analysis_status": p.get("analysis_status") or "complete",
           "extraction": p.get("extraction_report"), "ingest_version": p.get("ingest_version")}
    if authors is not None:
        out["authors"] = authors.lists.get(p["id"])
    return out

def _reviews_for(reviews: Optional[List[Dict]], kind: str, target_id: int) -> List[Dict]:
    out = []
    for r in reviews or []:
        if r["target_kind"] == kind and r["target_id"] == target_id:
            out.append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()})
    return out

def independence(label: str, pa: str, pb: str, authors: Optional[AuthorIndex], insufficient: bool) -> Optional[Dict]:
    """Dependence between the two papers behind a relationship/impact, and how prominently to qualify the label."""
    if authors is None:
        return None
    pair = authors.pair(pa, pb)
    return {**pair, **independence_flag(label, pair, insufficient)}

def claim_detail(c: Dict, papers: Dict[str, Dict], sentences: Dict[str, Dict]) -> Dict:
    p = papers.get(c["paper_id"], {})
    return {"id": c["id"], "paper_id": c["paper_id"], "paper_title": p.get("title", c.get("paper_title")),
            "access_level": p.get("access_level", c.get("access_level", "full")),
            "text": c["text"], "conditions": c.get("conditions") or {},
            "quotes": _quotes(c["sentence_ids"], sentences)}

def relationship_detail(r: Dict, claims: Dict[int, Dict], papers: Dict, sentences: Dict,
                        authors: Optional[AuthorIndex] = None, reviews: Optional[List[Dict]] = None) -> Dict:
    a, b = claims[r["claim_a"]], claims[r["claim_b"]]
    cited = [] if r.get("insufficient_evidence") else _quotes(r.get("cited_sentence_ids") or [], sentences)
    return {"id": r["id"], "label": r["label"], "similarity": r.get("similarity"),
            "model_label": r.get("model_label"), "comparability": r.get("comparability"),
            "label_note": r.get("label_note"),
            "independence": independence(r["label"], a["paper_id"], b["paper_id"], authors,
                                         bool(r.get("insufficient_evidence"))),
            "reviews": _reviews_for(reviews, "relationship", r["id"]),
            "insufficient_evidence": bool(r.get("insufficient_evidence")),
            "explanation": r.get("explanation", ""), "key_differences": r.get("key_differences") or [],
            "condition_diff": r.get("condition_diff") or {},
            "claim_a": claim_detail(a, papers, sentences), "claim_b": claim_detail(b, papers, sentences),
            "cited": {"a": [q for q in cited if q["paper_id"] == a["paper_id"]],
                      "b": [q for q in cited if q["paper_id"] == b["paper_id"]]}}

def ref_order(papers: List[Dict], claims: List[Dict]) -> List[str]:
    """Order in which not-yet-numbered nodes receive display numbers: papers by insertion time,
    each followed by its claims (by id). Only nodes without a number are affected, so this order
    never changes numbers that were already assigned."""
    by_paper: Dict[str, List[Dict]] = {}
    for c in sorted(claims, key=lambda c: c["id"]):
        by_paper.setdefault(c["paper_id"], []).append(c)
    out: List[str] = []
    for p in sorted(papers, key=lambda p: (str(p.get("created_at") or ""), p["id"])):
        out.append(f"paper:{p['id']}")
        out.extend(f"claim:{c['id']}" for c in by_paper.get(p["id"], []))
    return out

def build_graph(question: str, papers: List[Dict], claims: List[Dict], relationships: List[Dict],
                sentences: Dict[str, Dict], discovery: Optional[Dict] = None, source: str = "live",
                refs: Optional[Dict[str, int]] = None, authors: Optional[AuthorIndex] = None,
                reviews: Optional[List[Dict]] = None) -> Dict:
    """`refs` maps node ids to stable display numbers ("Node N"); nodes without one get no `ref`.
    `authors` (per-request author index) adds author lists and independence assessments; `reviews` adds the
    classification audit trail. Neither changes a stored label."""
    refs = refs or {}
    pmap = {p["id"]: paper_detail(p, authors) for p in papers}
    cmap = {c["id"]: c for c in claims}
    nodes = [{"data": {"id": "q", "type": "question", "label": question or "No research question set"}}]
    edges = []
    for p in pmap.values():
        nodes.append({"data": {"id": f"paper:{p['id']}", "type": "paper", "label": _short(p["title"], 60),
                               "paper_id": p["id"], "access_level": p["access_level"], "is_new": p["is_new"],
                               "relevance": p["relevance"], "ref": refs.get(f"paper:{p['id']}")}})
        # Navigation edge for the radial layout — not a scientific relationship.
        edges.append({"data": {"id": f"about:{p['id']}", "source": "q", "target": f"paper:{p['id']}",
                               "type": "question_paper", "relevance": p["relevance"]}})
    for c in claims:
        nodes.append({"data": {"id": f"claim:{c['id']}", "type": "claim", "label": _short(c["text"]),
                               "claim_id": c["id"], "paper_id": c["paper_id"], "paper_node": f"paper:{c['paper_id']}",
                               "is_new": bool(pmap.get(c["paper_id"], {}).get("is_new")),
                               "ref": refs.get(f"claim:{c['id']}")}})
        edges.append({"data": {"id": f"has:{c['id']}", "source": f"paper:{c['paper_id']}",
                               "target": f"claim:{c['id']}", "type": "has_claim"}})
    rels = [r for r in relationships if r["claim_a"] in cmap and r["claim_b"] in cmap]
    details = {str(r["id"]): relationship_detail(r, cmap, pmap, sentences, authors, reviews) for r in rels}
    for r in rels:
        d = details[str(r["id"])]
        edges.append({"data": {"id": f"rel:{r['id']}", "source": f"claim:{r['claim_a']}",
                               "target": f"claim:{r['claim_b']}", "type": "relationship", "label": r["label"],
                               "relationship_id": r["id"],
                               "insufficient_evidence": bool(r.get("insufficient_evidence")),
                               "independence": (d["independence"] or {}).get("level"),
                               "under_review": any(v["status"] == "proposed" for v in d["reviews"])}})
    disc_payload = None
    if discovery:
        anchor = f"paper:{discovery['for_paper_id']}" if discovery.get("for_paper_id") in pmap else "q"
        for i, d in enumerate(discovery.get("results", [])):
            nid = f"disc:{i}"
            nodes.append({"data": {"id": nid, "type": "discovery", "label": _short(d.get("title") or "Untitled", 50),
                                   "anchor": anchor, "index": i}})
            edges.append({"data": {"id": f"found:{i}", "source": anchor, "target": nid, "type": "discovered"}})
        disc_payload = discovery
    return {
        "question": question,
        "meta": {"source": source},
        "nodes": nodes,
        "edges": edges,
        "papers": pmap,
        "claims": {str(c["id"]): claim_detail(c, pmap, sentences) for c in claims},
        "relationships": details,
        "discovery": disc_payload,
    }

def build_impact_summary(new_paper: Dict, claims: List[Dict], impacts: List[Dict], sentences: Dict[str, Dict],
                         papers: List[Dict], extraction: Optional[Dict] = None, status: str = "live",
                         authors: Optional[AuthorIndex] = None, reviews: Optional[List[Dict]] = None) -> Dict:
    pmap = {p["id"]: paper_detail(p, authors) for p in papers}
    cmap = {c["id"]: c for c in claims}
    new_claims = [c for c in claims if c["paper_id"] == new_paper["id"]]
    new_ids = {c["id"] for c in new_claims}
    prior_claims = [c for c in claims if c["paper_id"] != new_paper["id"]]
    items = []
    for i in impacts:
        # Only impacts produced by THIS paper's claims; earlier uploads never leak into this report.
        if i["new_claim_id"] not in new_ids or i["prior_claim_id"] not in cmap:
            continue
        if cmap[i["prior_claim_id"]]["paper_id"] == new_paper["id"]:
            continue
        insufficient = bool(i.get("insufficient_evidence"))
        items.append({
            "id": i["id"], "impact": i["impact"], "rationale": i.get("rationale", ""),
            "insufficient_evidence": insufficient, "relationship_id": i.get("relationship_id"),
            "independence": independence(i["impact"], new_paper["id"], cmap[i["prior_claim_id"]]["paper_id"],
                                         authors, insufficient),
            "reviews": _reviews_for(reviews, "impact", i["id"]),
            "prior_claim": claim_detail(cmap[i["prior_claim_id"]], pmap, sentences),
            "new_claim": claim_detail(cmap[i["new_claim_id"]], pmap, sentences),
            "cited": [] if insufficient else _quotes(i.get("cited_sentence_ids") or [], sentences),
        })
    addressed = {i["prior_claim"]["id"] for i in items}
    counts = {k: sum(1 for i in items if i["impact"] == k) for k in IMPACTS}
    not_addressed = [claim_detail(c, pmap, sentences) for c in prior_claims if c["id"] not in addressed]
    counts["not_addressed"] = len(not_addressed)
    return {
        "status": status,
        "paper": pmap.get(new_paper["id"], paper_detail(new_paper, authors)),
        "new_claims": [claim_detail(c, pmap, sentences) for c in new_claims],
        "counts": counts,
        "impacts": items,
        "not_addressed": not_addressed,
        "extraction": extraction,
    }