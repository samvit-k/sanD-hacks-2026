"""Record classification review PROPOSALS (audit trail) for one research session. See app/reviews.py.

Proposals never change a stored relationship/impact label; a named person accepts or rejects them later
(POST /api/reviews/{id}/decision). Dry run by default; pass --apply to write. Nothing is written if any proposal's
target (claim pair, label, optional fingerprint) does not match what is stored.

Input JSON:
  [{"target_kind": "relationship", "target_id": 9, "expected_label": "supports", "expected_claims": [1, 9],
    "expected_fingerprint": "<optional; printed by a dry run>",
    "proposed_label": "not_comparable", "reason": "...", "evidence": {...}}]

    python scripts/propose_reviews.py --session <id> --file proposals.json --reviewer-type model \
        --reviewer "…" [--apply]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, reviews  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--reviewer-type", required=True, choices=db.REVIEWER_TYPES)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    proposals = json.loads(Path(args.file).read_text())
    with db.use_session(args.session), db.connect() as conn:
        report = reviews.record_proposals(conn, proposals, args.reviewer_type, args.reviewer, args.apply)
        if not args.apply:
            conn.rollback()
    for r in report["rejected"]:
        print(f"REJECTED {r['target_kind']} {r['target_id']}: {'; '.join(r['problems'])}")
    if report["rejected"]:
        print("nothing written: fix the proposals above")
        return 1
    for a in report["actions"]:
        print(f"{a['action']}: {a['target_kind']} {a['target_id']} claims {a['claims']} "
              f"{a['original_label']} -> {a['proposed_label']} (fingerprint {a['fingerprint'][:16]}…)"
              + (f" — {a['note']}" if a.get("note") else ""))
    if not args.apply:
        print("dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
