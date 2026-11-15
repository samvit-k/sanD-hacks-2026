"""Pure builders for the two frontend contracts: the graph payload and the impact summary.

Both take plain rows (dicts) so the live API, the fixture generator and tests share one code path.
Every quote is rebuilt from stored sentences; unverified IDs are never emitted.
"""
from typing import Dict, List, Optional

from .contracts import IMPACTS


def _quotes(ids: List[str], sentences: Dict[str, Dict]) -> List[Dict]:
    return [
        {"id": sid, "paper_id": sentences[sid]["paper_id"], "page": sentences[sid]["page"],
         "section": sentences[sid].get("section", "front"), "text": sentences[sid]["text"]}
        for sid in ids if sid in sentences
    ]


def _short(text: str, n: int = 70) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def paper_detail(p: Dict) -> Dict:
    return {"id": p["id"], "title": p["title"], "doi": p.get("doi"), "url": p.get("url"), "year": p.get("year"),
            "access_level": p.get("access_level", "full"), "license": p.get("license"),
            "is_new": bool(p.get("is_new")), "relevance": p.get("relevance")}


def claim_detail(c: Dict, papers: Dict[str, Dict], sentences: Dict[str, Dict]) -> Dict:
    p = papers.get(c["paper_id"], {})
    return {"id": c["id"], "paper_id": c["paper_id"], "paper_title": p.get("title", c.get("paper_title")),
            "access_level": p.get("access_level", c.get("access_level", "full")),
            "text": c["text"], "conditions": c.get("conditions") or {},
            "quotes": _quotes(c["sentence_ids"], sentences)}


def relationship_detail(r: Dict, claims: Dict[int, Dict], papers: Dict, sentences: Dict) -> Dict:
    a, b = claims[r["claim_a"]], claims[r["claim_b"]]
    cited = [] if r.get("insufficient_evidence") else _quotes(r.get("cited_sentence_ids") or [], sentences)
    return {"id": r["id"], "label": r["label"], "similarity": r.get("similarity"),
            "insufficient_evidence": bool(r.get("insufficient_evidence")),
            "explanation": r.get("explanation", ""), "key_differences": r.get("key_differences") or [],
            "condition_diff": r.get("condition_diff") or {},
            "claim_a": claim_detail(a, papers, sentences), "claim_b": claim_detail(b, papers, sentences),
            "cited": {"a": [q for q in cited if q["paper_id"] == a["paper_id"]],
                      "b": [q for q in cited if q["paper_id"] == b["paper_id"]]}}


def build_graph(question: str, papers: List[Dict], claims: List[Dict], relationships: List[Dict],
                sentences: Dict[str, Dict], discovery: Optional[Dict] = None, source: str = "live") -> Dict:
    pmap = {p["id"]: paper_detail(p) for p in papers}
    cmap = {c["id"]: c for c in claims}
    nodes = [{"data": {"id": "q", "type": "question", "label": question or "No research question set"}}]
    edges = []
    for p in pmap.values():
        nodes.append({"data": {"id": f"paper:{p['id']}", "type": "paper", "label": _short(p["title"], 60),
                               "paper_id": p["id"], "access_level": p["access_level"], "is_new": p["is_new"],
                               "relevance": p["relevance"]}})
        # Navigation edge for the radial layout — not a scientific relationship.
        edges.append({"data": {"id": f"about:{p['id']}", "source": "q", "target": f"paper:{p['id']}",
                               "type": "question_paper", "relevance": p["relevance"]}})
    for c in claims:
        nodes.append({"data": {"id": f"claim:{c['id']}", "type": "claim", "label": _short(c["text"]),
                               "claim_id": c["id"], "paper_id": c["paper_id"], "paper_node": f"paper:{c['paper_id']}",
                               "is_new": bool(pmap.get(c["paper_id"], {}).get("is_new"))}})
        edges.append({"data": {"id": f"has:{c['id']}", "source": f"paper:{c['paper_id']}",
                               "target": f"claim:{c['id']}", "type": "has_claim"}})
    rels = [r for r in relationships if r["claim_a"] in cmap and r["claim_b"] in cmap]
    for r in rels:
        edges.append({"data": {"id": f"rel:{r['id']}", "source": f"claim:{r['claim_a']}",
                               "target": f"claim:{r['claim_b']}", "type": "relationship", "label": r["label"],
                               "relationship_id": r["id"],
                               "insufficient_evidence": bool(r.get("insufficient_evidence"))}})
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
        "relationships": {str(r["id"]): relationship_detail(r, cmap, pmap, sentences) for r in rels},
        "discovery": disc_payload,
    }


def build_impact_summary(new_paper: Dict, claims: List[Dict], impacts: List[Dict], sentences: Dict[str, Dict],
                         papers: List[Dict], extraction: Optional[Dict] = None, status: str = "live") -> Dict:
    pmap = {p["id"]: paper_detail(p) for p in papers}
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
        "paper": pmap.get(new_paper["id"], paper_detail(new_paper)),
        "new_claims": [claim_detail(c, pmap, sentences) for c in new_claims],
        "counts": counts,
        "impacts": items,
        "not_addressed": not_addressed,
        "extraction": extraction,
    }
