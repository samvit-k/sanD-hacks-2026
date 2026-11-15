"""Save demo fallbacks from the REAL pipeline output into frontend/public/fixtures/.

  .venv/bin/python scripts/snapshot.py --stage before   # after seeding:  graph.json
  .venv/bin/python scripts/snapshot.py --stage after    # after Paper D:  graph_with_new.json + paper_d_result.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, pipeline  # noqa: E402
from app.config import ROOT  # noqa: E402

OUT = ROOT / "frontend" / "public" / "fixtures"


def write(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, indent=1, default=str))
    print(f"wrote {OUT / name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["before", "after"], required=True)
    args = ap.parse_args()
    with db.connect() as conn:
        v = db.integrity_violations(conn)
        latest = db.latest_new_paper(conn)
    if any(v.values()):
        raise SystemExit(f"Refusing to snapshot: integrity violations {v}")
    graph = pipeline.graph_payload()
    if args.stage == "before":
        graph["meta"]["source"] = "snapshot"
        write("graph.json", graph)
        return
    if not latest:
        raise SystemExit("No new paper evaluated yet (run seed.py --new ...)")
    graph["meta"]["source"] = "snapshot"
    write("graph_with_new.json", graph)
    write("paper_d_result.json", pipeline.impact_summary(latest["id"], status="precomputed"))


if __name__ == "__main__":
    main()
