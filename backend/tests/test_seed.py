"""Seeding must be idempotent and never delete unrelated data unless explicitly asked."""
import json

import pytest

import seed as seed_mod
from app import db, extract, pipeline
from app.config import settings
from app.contracts import ExtractionResult

from .fake_llm import FakeLLM
from .test_pipeline import PAPER_D, PAPERS, pdf

EXTRACT = "You are extracting testable scientific findings"
PAIR = "You are verifying the relationship"
QUIET = lambda *_: None  # noqa: E731

@pytest.fixture
def manifest(tmp_path, clean_db, monkeypatch):
    monkeypatch.setattr(settings, "sim_threshold", 0.2)
    pipeline._question_vecs.clear()
    entries = []
    for pid, (title, text) in list(PAPERS.items())[:2]:
        (tmp_path / f"{pid}.pdf").write_bytes(pdf(text))
        entries.append({"id": pid, "file": f"{pid}.pdf", "title": title, "doi": f"10.1/{pid}"})
    pid, (title, text) = list(PAPERS.items())[2]
    entries.append({"id": pid, "title": title, "abstract": text.replace("Results\n", "").replace("Conclusion\n", "")})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"papers": entries}))
    return path

def rows():
    with db.connect() as conn:
        return {"papers": db.all_papers(conn), "claims": db.all_claims(conn),
                "rels": db.all_relationships(conn), "impacts": conn.execute("SELECT * FROM impacts").fetchall()}

def snapshot():
    r = rows()
    return {"papers": sorted(p["id"] for p in r["papers"]), "claims": sorted(c["id"] for c in r["claims"]),
            "rels": sorted(x["id"] for x in r["rels"]), "impacts": sorted(i["id"] for i in r["impacts"])}

def test_report_fields_used_by_seed_exist():
    _, report = extract.validate_claims(ExtractionResult(claims=[]), "p", {}, 3)
    for key in ("kept", "rejected_invalid_citations", "rejected_empty_text", "rejected_over_limit"):
        assert key in report

def test_seed_then_rerun_is_idempotent_and_keeps_uploads(manifest):
    llm = FakeLLM()
    out = []
    stats = seed_mod.seed(llm, manifest, out=out.append)
    assert stats["added"] == ["paper-a", "paper-b", "paper-c"] and not stats["violations"]
    assert any("claims kept" in line and "invalid citations" in line for line in out)
    with db.connect() as conn:
        assert db.get_paper(conn, "paper-c")["access_level"] == "abstract"
    pipeline.process_new_paper(llm, pdf(PAPER_D[1]), title=PAPER_D[0])
    before = snapshot()
    assert before["impacts"]
    extractions, pairs = llm.count(EXTRACT), llm.count(PAIR)

    stats = seed_mod.seed(llm, manifest, out=QUIET)  # plain re-run
    assert stats["skipped"] == ["paper-a", "paper-b", "paper-c"] and stats["added"] == []
    assert snapshot() == before  # nothing deleted; uploaded Paper D and its impacts intact
    assert llm.count(EXTRACT) == extractions and llm.count(PAIR) == pairs  # no wasted Gemini calls

def test_refresh_updates_judgments_in_place(manifest):
    seed_mod.seed(FakeLLM(), manifest, out=QUIET)
    pipeline.process_new_paper(FakeLLM(), pdf(PAPER_D[1]), title=PAPER_D[0])
    before = snapshot()
    stats = seed_mod.seed(FakeLLM(pair_label="supports"), manifest, refresh=True, out=QUIET)
    assert stats["compared"] > 0 and snapshot() == before  # same rows, same ids, impacts intact
    with db.connect() as conn:
        assert {r["label"] for r in db.all_relationships(conn)} == {"supports"}

def test_reextract_replaces_claims_without_duplicates_or_stale_rows(manifest):
    llm = FakeLLM()
    seed_mod.seed(llm, manifest, out=QUIET)
    pipeline.process_new_paper(llm, pdf(PAPER_D[1]), title=PAPER_D[0])
    before = rows()
    old_a = {c["id"] for c in before["claims"] if c["paper_id"] == "paper-a"}
    old_a_texts = sorted(c["text"] for c in before["claims"] if c["paper_id"] == "paper-a")
    other_claims = {c["id"] for c in before["claims"] if c["paper_id"] != "paper-a"}
    unrelated_rels = {r["id"] for r in before["rels"] if r["claim_a"] not in old_a and r["claim_b"] not in old_a}
    unrelated_impacts = {i["id"] for i in before["impacts"] if i["prior_claim_id"] not in old_a}
    assert old_a and any(r["claim_a"] in old_a or r["claim_b"] in old_a for r in before["rels"])

    out = []
    stats = seed_mod.seed(llm, manifest, reextract=["paper-a"], out=out.append)
    assert stats["reextracted"] == ["paper-a"] and stats["skipped"] == ["paper-b", "paper-c"]
    assert not stats["violations"] and any("WARNING" in line for line in out)

    after = rows()
    ids = {c["id"] for c in after["claims"]}
    new_a = {c["id"] for c in after["claims"] if c["paper_id"] == "paper-a"}
    # Replaced, not duplicated: same claim texts, new ids, old ids gone.
    assert sorted(c["text"] for c in after["claims"] if c["paper_id"] == "paper-a") == old_a_texts
    assert new_a and not (new_a & old_a) and not (old_a & ids)
    assert len([p for p in after["papers"] if p["id"] == "paper-a"]) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sentences WHERE paper_id='paper-a' GROUP BY id "
                            "HAVING count(*) > 1").fetchall() == []
    # No stale relationships/impacts pointing at the old claims; no duplicate pairs.
    assert all(r["claim_a"] in ids and r["claim_b"] in ids for r in after["rels"])
    assert all(i["prior_claim_id"] in ids and i["new_claim_id"] in ids for i in after["impacts"])
    pairs = [(r["claim_a"], r["claim_b"]) for r in after["rels"]]
    assert len(pairs) == len(set(pairs))
    # The new paper-a claims were re-compared.
    assert any(r["claim_a"] in new_a or r["claim_b"] in new_a for r in after["rels"])
    # Unrelated data preserved exactly.
    assert other_claims <= ids
    assert unrelated_rels <= {r["id"] for r in after["rels"]}
    assert unrelated_impacts <= {i["id"] for i in after["impacts"]}
    assert any(p["is_new"] for p in after["papers"])

def test_reextract_unknown_id_rejected(manifest):
    with pytest.raises(SystemExit):
        seed_mod.seed(FakeLLM(), manifest, reextract=["nope"], out=QUIET)

def test_reset_requires_confirmation(manifest, monkeypatch):
    seed_mod.seed(FakeLLM(), manifest, out=QUIET)
    before = snapshot()
    monkeypatch.setattr(seed_mod, "get_llm", lambda: FakeLLM())
    assert seed_mod.main(["--reset", "--manifest", str(manifest)]) == 2
    assert snapshot() == before
    assert seed_mod.main(["--reset", "--yes", "--manifest", str(manifest)]) == 0
    assert snapshot()["papers"] == ["paper-a", "paper-b", "paper-c"]

def test_new_exit_code_reflects_integrity_checks(manifest, tmp_path, monkeypatch):
    seed_mod.seed(FakeLLM(), manifest, out=QUIET)
    monkeypatch.setattr(seed_mod, "get_llm", lambda: FakeLLM())
    d = tmp_path / "d.pdf"
    d.write_bytes(pdf(PAPER_D[1]))
    assert seed_mod.main(["--new", str(d), "--title", PAPER_D[0]]) == 0
    monkeypatch.setattr(db, "integrity_violations",
                        lambda conn: {"claims_with_bad_citations": [1], "impacts_with_bad_citations": []})
    assert seed_mod.main(["--new", str(d), "--title", PAPER_D[0]]) == 1