import psycopg
import pytest

from app import db

def _seed_two_claims(conn):
    for pid in ("pa", "pb"):
        db.insert_paper(conn, {"id": pid, "title": f"Paper {pid}"})
        db.insert_sentences(conn, [{"id": f"{pid}:p1_s1", "paper_id": pid, "page": 1, "idx": 1,
                                    "section": "results", "text": f"{pid} result sentence."}])
    a = db.insert_claim(conn, {"paper_id": "pa", "text": "A", "conditions": {}, "sentence_ids": ["pa:p1_s1"]}, [1.0] + [0.0] * 767)
    b = db.insert_claim(conn, {"paper_id": "pb", "text": "B", "conditions": {}, "sentence_ids": ["pb:p1_s1"]}, [0.9, 0.1] + [0.0] * 766)
    return a, b

def _rel(a, b, label="supports", diff=None):
    return {"claim_a": a, "claim_b": b, "label": label, "similarity": 0.9, "explanation": label,
            "condition_diff": diff or {}, "key_differences": [], "cited_sentence_ids": ["pa:p1_s1", "pb:p1_s1"],
            "insufficient_evidence": False, "model": "test"}

def test_normalize_pair_order_flips_condition_diff():
    r = db.normalize_pair_order(_rel(7, 3, diff={"duration": ["12 weeks", "2 weeks"], "population": "same"}))
    assert (r["claim_a"], r["claim_b"]) == (3, 7)
    assert r["condition_diff"] == {"duration": ["2 weeks", "12 weeks"], "population": "same"}
    assert db.normalize_pair_order(_rel(3, 7))["claim_a"] == 3
    with pytest.raises(ValueError):
        db.normalize_pair_order(_rel(3, 3))

def test_relationship_upsert_orders_and_updates(clean_db):
    with db.connect() as conn:
        a, b = _seed_two_claims(conn)
        rid = db.upsert_relationship(conn, _rel(b, a, "supports", {"duration": ["B dur", "A dur"]}))  # reversed
        row = conn.execute("SELECT * FROM relationships WHERE id = %s", (rid,)).fetchone()
        assert (row["claim_a"], row["claim_b"]) == (a, b)
        assert row["condition_diff"] == {"duration": ["A dur", "B dur"]}
        # Re-evaluation refreshes the same row instead of being silently ignored.
        rid2 = db.upsert_relationship(conn, _rel(a, b, "apparent_contradiction"))
        assert rid2 == rid
        row = conn.execute("SELECT * FROM relationships WHERE id = %s", (rid,)).fetchone()
        assert row["label"] == "apparent_contradiction"
        assert conn.execute("SELECT count(*) AS n FROM relationships").fetchone()["n"] == 1

def test_impact_upsert_refreshes_relationship_id(clean_db):
    with db.connect() as conn:
        a, b = _seed_two_claims(conn)
        r1 = db.upsert_relationship(conn, _rel(a, b))
        imp = {"new_claim_id": b, "prior_claim_id": a, "relationship_id": None, "impact": "strengthens",
               "rationale": "r", "cited_sentence_ids": ["pa:p1_s1", "pb:p1_s1"], "insufficient_evidence": False,
               "model": "t"}
        iid = db.upsert_impact(conn, imp)
        iid2 = db.upsert_impact(conn, {**imp, "relationship_id": r1, "impact": "qualifies"})
        assert iid2 == iid
        row = conn.execute("SELECT * FROM impacts WHERE id = %s", (iid,)).fetchone()
        assert row["relationship_id"] == r1 and row["impact"] == "qualifies"

def test_schema_rejects_unordered_pair_and_empty_citations(clean_db):
    with db.connect() as conn:
        a, b = _seed_two_claims(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as conn:
            conn.execute("INSERT INTO relationships (claim_a, claim_b, label) VALUES (%s, %s, 'supports')", (b, a))
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as conn:
            conn.execute("INSERT INTO claims (paper_id, text, sentence_ids) VALUES ('pa', 'x', '{}')")

def test_candidate_pairs_only_other_papers(clean_db):
    with db.connect() as conn:
        a, b = _seed_two_claims(conn)
        rows = db.candidate_pairs(conn, [a], top_k=2)
        assert [(r["a"], r["b"]) for r in rows] == [(a, b)]
        assert rows[0]["sim"] > 0.9

def test_integrity_checks_detect_bad_citations(clean_db):
    with db.connect() as conn:
        a, b = _seed_two_claims(conn)
        db.upsert_relationship(conn, _rel(a, b))
        assert all(not v for v in db.integrity_violations(conn).values())
        # Simulate a corrupted row: claim citing another paper's sentence.
        conn.execute("UPDATE claims SET sentence_ids = '{pb:p1_s1}' WHERE id = %s", (a,))
        assert db.integrity_violations(conn)["claims_with_bad_citations"] == [a]