"""Compare Gemini's relationship labels / impacts with the team's manual gold set (eval/gold_pairs.json).

Claims are matched by paper id + a distinctive substring of the claim text, so the gold file
survives re-seeding (claim ids change). Target: >= 7/10 agreement.

  .venv/bin/python scripts/eval_gold.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.config import ROOT  # noqa: E402

GOLD = ROOT / "eval" / "gold_pairs.json"


def find_claim(claims, ref):
    hits = [c for c in claims if c["paper_id"] == ref["paper_id"] and ref["contains"].lower() in c["text"].lower()]
    return hits[0] if len(hits) == 1 else None


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True, help="research session id (GET /api/sessions)")
    args = ap.parse_args()
    with db.use_session(db.get_session(args.session)["id"]):
        _evaluate()


def _evaluate() -> None:
    gold = json.loads(GOLD.read_text())
    with db.connect() as conn:
        claims = db.all_claims(conn)
        rels = {(r["claim_a"], r["claim_b"]): r for r in db.all_relationships(conn)}
        impacts = {(i["new_claim_id"], i["prior_claim_id"]): i
                   for i in conn.execute("SELECT * FROM impacts").fetchall()}
    agree = total = 0
    for g in gold.get("pairs", []):
        a, b = find_claim(claims, g["claim_a"]), find_claim(claims, g["claim_b"])
        if not a or not b:
            print(f"? {g['id']}: claim not found (check paper_id/contains)")
            continue
        r = rels.get((min(a["id"], b["id"]), max(a["id"], b["id"])))
        got = r["label"] if r else "no relationship (not matched)"
        ok = got == g["label"]
        agree += ok
        total += 1
        print(f"{'✓' if ok else '✗'} {g['id']}: gold={g['label']} model={got}  — {g.get('reason', '')}")
    for g in gold.get("impacts", []):
        new, prior = find_claim(claims, g["new_claim"]), find_claim(claims, g["prior_claim"])
        if not new or not prior:
            print(f"? {g['id']}: claim not found")
            continue
        i = impacts.get((new["id"], prior["id"]))
        got = i["impact"] if i else "not addressed"
        ok = got == g["impact"]
        agree += ok
        total += 1
        print(f"{'✓' if ok else '✗'} {g['id']}: gold={g['impact']} model={got}  — {g.get('reason', '')}")
    if total:
        print(f"\nagreement: {agree}/{total} ({agree / total:.0%}); target >= 70%")


if __name__ == "__main__":
    main()
