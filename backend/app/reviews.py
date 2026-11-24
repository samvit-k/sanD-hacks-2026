"""Recording classification review proposals (audit trail) with verified target identity.

Proposals never change a stored relationship/impact label; a named person accepts or rejects them later.
Ids are only meaningful within the current analysis (relationships can be regenerated, and several can share a
label), so before anything is written each proposal's target is checked against:
  - expected_claims (required): the relationship's claim pair (order-insensitive) or, for an impact,
    [new_claim_id, prior_claim_id];
  - expected_label (required): the stored label;
  - expected_fingerprint (optional): sha256 over kind, claim ids, claim texts, label and cited sentence ids.
The fingerprint is stored with every review (evidence.target_fingerprint) and is part of duplicate detection:
the same proposal for CHANGED target evidence is a new proposal. A stored review without a fingerprint (recorded
before fingerprints existed) cannot be compared, so a matching proposal is skipped and reported for manual
checking rather than risk a duplicate.
"""
import hashlib
import json
from typing import Dict, List, Optional, Tuple

from . import db

TARGETS = {
    "relationship": "SELECT id, label, claim_a AS c1, claim_b AS c2, cited_sentence_ids FROM relationships WHERE id = %s",
    "impact": "SELECT id, impact AS label, new_claim_id AS c1, prior_claim_id AS c2, cited_sentence_ids "
              "FROM impacts WHERE id = %s",
}

def target_fingerprint(kind: str, row: Dict, claim_texts: Dict[int, str]) -> str:
    payload = {"kind": kind, "claims": [row["c1"], row["c2"]],
               "claim_texts": [claim_texts.get(row["c1"]), claim_texts.get(row["c2"])],
               "label": row["label"], "cited_sentence_ids": list(row["cited_sentence_ids"] or [])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def verify_target(conn, p: Dict) -> Tuple[Optional[Dict], Optional[str], List[str]]:
    """(row, fingerprint, problems). Any problem means the proposal does not match the stored target."""
    kind = p.get("target_kind")
    if kind not in TARGETS:
        return None, None, [f"unknown target_kind {kind!r}"]
    row = conn.execute(TARGETS[kind], (p.get("target_id"),)).fetchone()
    if not row:
        return None, None, [f"{kind} {p.get('target_id')} does not exist"]
    texts = {c["id"]: c["text"] for c in conn.execute("SELECT id, text FROM claims WHERE id = ANY(%s)",
                                                       ([row["c1"], row["c2"]],)).fetchall()}
    fp = target_fingerprint(kind, row, texts)
    problems = []
    expected = p.get("expected_claims")
    if not expected or len(expected) != 2:
        problems.append("expected_claims (two claim ids) is required")
    else:
        stored = [row["c1"], row["c2"]]
        same = sorted(stored) == sorted(expected) if kind == "relationship" else stored == list(expected)
        if not same:
            problems.append(f"claims are {stored}, expected {list(expected)}")
    if row["label"] != p.get("expected_label"):
        problems.append(f"stored label is {row['label']!r}, expected {p.get('expected_label')!r}")
    if p.get("expected_fingerprint") and p["expected_fingerprint"] != fp:
        problems.append("fingerprint differs: the target's claims, label or citations changed")
    if not (p.get("reason") or "").strip():
        problems.append("reason is required")
    return row, fp, problems

def _key(kind, target_id, proposed, reason, fp) -> Tuple:
    return (kind, target_id, proposed, reason, fp)

def record_proposals(conn, proposals: List[Dict], reviewer_type: str, reviewer: str, apply: bool) -> Dict:
    """Verify every proposal first; write nothing if any target does not match. Returns a report."""
    if reviewer_type not in db.REVIEWER_TYPES:
        raise ValueError("reviewer_type must be automated, model or human")
    checked = [(p, *verify_target(conn, p)) for p in proposals]
    rejected = [{"target_kind": p.get("target_kind"), "target_id": p.get("target_id"), "problems": probs}
                for p, _, _, probs in checked if probs]
    report = {"rejected": rejected, "actions": []}
    if rejected:
        return report
    existing = set()
    legacy = set()  # reviews stored without a fingerprint: comparable only without it
    for r in db.all_reviews(conn):
        fp = (r.get("evidence") or {}).get("target_fingerprint")
        if fp:
            existing.add(_key(r["target_kind"], r["target_id"], r["proposed_label"], r["reason"], fp))
        else:
            legacy.add(_key(r["target_kind"], r["target_id"], r["proposed_label"], r["reason"], None))
    for p, row, fp, _ in checked:
        key = _key(p["target_kind"], p["target_id"], p.get("proposed_label"), p["reason"], fp)
        base = {"target_kind": p["target_kind"], "target_id": p["target_id"], "claims": [row["c1"], row["c2"]],
                "original_label": row["label"], "proposed_label": p.get("proposed_label"), "fingerprint": fp}
        if key in existing:
            report["actions"].append({**base, "action": "skip_duplicate"})
            continue
        if key[:4] + (None,) in legacy:
            report["actions"].append({**base, "action": "skip_unverifiable_existing",
                                      "note": "an identical proposal was stored before fingerprints existed; "
                                              "check it manually"})
            continue
        if apply:
            db.add_review(conn, {"target_kind": p["target_kind"], "target_id": p["target_id"],
                                 "original_label": row["label"], "proposed_label": p.get("proposed_label"),
                                 "reason": p["reason"], "reviewer_type": reviewer_type, "reviewer": reviewer,
                                 "evidence": {**(p.get("evidence") or {}), "target_fingerprint": fp}})
        existing.add(key)  # identical proposals repeated in the same input are recorded once
        report["actions"].append({**base, "action": "recorded" if apply else "would_record"})
    return report