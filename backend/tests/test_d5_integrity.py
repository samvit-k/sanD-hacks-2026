"""Stage D5: shared authorship / evidence independence, conservative relationship labels, and the review audit trail.

Front-matter passages are the real stored first-page sentences of the D4 and original-project papers.
"""
import psycopg
import pytest

from app import authors as A
from app import chat, compare, db, graph, reviews
from app.contracts import ChatAnswer, PairJudgment

NRMS_T = "Neural News Recommendation with Multi-Head Self-Attention"
NRMS = ("Proceedings of the 2019 Conference on Empirical Methods in Natural Language Processing and the 9th "
        "International Joint Conference on Natural Language Processing, pages 6389–6394, Hong Kong, China, November "
        "3–7, 2019. c⃝2019 Association for Computational Linguistics Neural News Recommendation with Multi-Head "
        "Self-Attention Chuhan Wu1, Fangzhao Wu2, Suyu Ge1, Tao Qi1, Yongfeng Huang1,and Xing Xie2 1Department of "
        "Electronic Engineering, Tsinghua University, Beijing 100084, China 2Microsoft Research Asia, Beijing 100080, "
        "China {wu-ch19, gsy17, qit16, yfhuang}@mails.tsinghua.edu.cn, {fangzwu, xing.xie}@microsoft.com")
MIND_T = "MIND: A Large-scale Dataset for News Recommendation"
MIND = ("Proceedings of the 58th Annual Meeting of the Association for Computational Linguistics, pages 3597–3606 "
        "July 5 - 10, 2020. c⃝2020 Association for Computational Linguistics MIND: A Large-scale Dataset for News "
        "Recommendation Fangzhao Wu†, Ying Qiao‡, Jiun-Hung Chen‡, Chuhan Wu§, Tao Qi§, Jianxun Lian†, Danyang Liu†, "
        "Xing Xie†, Jianfeng Gao†, Winnie Wu‡, Ming Zhou† †Microsoft Research ‡Microsoft §Tsinghua University "
        "{fangzwu, yiqia, jiuche, jialia}@microsoft.com {t-danliu, xingx, jfgao, winniew, mingzhou}@microsoft.com")
LAMP_T = "LaMP: When Large Language Models Meet Personalization"
LAMP = ("LaMP: When Large Language Models Meet Personalization Alireza Salemi1, Sheshera Mysore1, Michael Bendersky2, "
        "Hamed Zamani1 1University of Massachusetts Amherst 2Google Research {asalemi,smysore,zamani}@cs.umass.edu")
K12_T = "Intelligent Tutors Beyond K-12: An Observational Study of Adult Learner Engagement and Academic Impact"
K12 = (K12_T + " Adit Gupta Drexel University Philadelphia, PA, USA Christopher MacLellan Georgia Institute of "
       "Technology Atlanta, GA, USA")
QP_T = "Question Personalization in an Intelligent Tutoring System"
QP = (QP_T + " Sabina Elkins1,2, Robert Belfer2, Ekaterina Kochmar2,3, Iulian Serban2, and Jackie C.K. Cheung1,4 "
      "1 McGill University & MILA (Quebec Artificial Intelligence Institute) 2 Korbit Technologies Inc. Abstract.")
APF_T = "Automated Personalized Feedback Improves Learning Gains in an Intelligent Tutoring System"
APF = (APF_T + " Ekaterina Kochmar1,2, Dung Do Vu1,3, Robert Belfer1, Varun Gupta1, Iulian Vlad Serban1, and Joelle "
       "Pineau1,4 1 Korbit Technologies Inc. 3 ́Ecole de Technologie Sup ́erieure 4 McGill University Abstract.")


def authors_of(pid, title, text):
    return A.paper_authors({"id": pid, "title": title}, [{"id": f"{pid}:p1_s1", "text": text}])


# ---------- author lists ----------
def test_author_lists_are_parsed_from_real_front_matter():
    assert authors_of("nrms", NRMS_T, NRMS)["names"] == ["Chuhan Wu", "Fangzhao Wu", "Suyu Ge", "Tao Qi",
                                                          "Yongfeng Huang", "Xing Xie"]
    mind = authors_of("mind", MIND_T, MIND)
    assert mind["status"] == A.PARSED and len(mind["names"]) == 11 and mind["source_sentence_id"] == "mind:p1_s1"
    assert authors_of("lamp", LAMP_T, LAMP)["names"] == ["Alireza Salemi", "Sheshera Mysore", "Michael Bendersky",
                                                          "Hamed Zamani"]
    assert authors_of("qp", QP_T, QP)["names"][-1] == "Jackie C.K. Cheung"


def test_ambiguous_author_blocks_are_unknown_not_empty():
    k12 = authors_of("k12", K12_T, K12)  # names and affiliations run together without separators
    assert k12["status"] == A.UNKNOWN and "run together" in k12["reason"]
    assert authors_of("x", "Some Other Title", NRMS)["status"] == A.UNKNOWN  # title not in the sentence


def test_shared_authors_need_full_given_name_and_surname():
    pair = A.shared_authors(authors_of("nrms", NRMS_T, NRMS), authors_of("mind", MIND_T, MIND))
    assert pair["status"] == A.SHARED
    assert sorted(pair["shared"]) == ["Chuhan Wu", "Fangzhao Wu", "Tao Qi", "Xing Xie"]  # Winnie Wu is not matched
    assert pair["independence_established"] is False and pair["review_needed"]
    assert pair["basis_sentence_ids"] == ["nrms:p1_s1", "mind:p1_s1"]
    assert "does not by itself mean the same data" in pair["summary"]


def test_middle_names_may_be_missing_but_initials_only_give_possible_matches():
    pair = A.shared_authors(authors_of("qp", QP_T, QP), authors_of("apf", APF_T, APF))
    assert "Iulian Serban / Iulian Vlad Serban" in pair["shared"] and len(pair["shared"]) == 3
    assert A.match_names("C. Wu", "Chuhan Wu") == "possible"
    assert A.match_names("Paulo F. Carvalho", "Paulo G. Carvalho") is None
    a = {"status": A.PARSED, "names": ["C. Wu"], "source_sentence_id": "a:p1_s1"}
    b = {"status": A.PARSED, "names": ["Chuhan Wu"], "source_sentence_id": "b:p1_s1"}
    assert A.shared_authors(a, b)["status"] == A.POSSIBLE


def test_no_detected_overlap_is_not_independence_and_unknown_is_not_none():
    none = A.shared_authors(authors_of("nrms", NRMS_T, NRMS), authors_of("lamp", LAMP_T, LAMP))
    assert none["status"] == A.NONE_DETECTED and none["independence_established"] is False
    assert "does not establish independence" in none["summary"]
    unknown = A.shared_authors(authors_of("k12", K12_T, K12), authors_of("lamp", LAMP_T, LAMP))
    assert unknown["status"] == A.PAIR_UNKNOWN and unknown["review_needed"]


def test_independence_flag_qualifies_only_supportive_labels():
    shared = {"status": A.SHARED}
    assert A.independence_flag("supports", shared)["headline"] == A.REVIEW_HEADLINE
    assert A.independence_flag("strengthens", {"status": A.PAIR_UNKNOWN})["level"] == "review"
    assert A.independence_flag("strengthens", {"status": A.NONE_DETECTED})["level"] == "note"
    assert A.independence_flag("not_comparable", shared)["level"] is None
    assert A.independence_flag("supports", shared, insufficient=True)["level"] is None


# ---------- relationship classification ----------
STORED = {"pa:p1_s1": "pa", "pb:p1_s1": "pb"}


def judgment(label, comparability):
    return PairJudgment(comparability=comparability, label=label, explanation="Both improve performance.",
                        key_differences=[], cited_sentence_ids=["pa:p1_s1", "pb:p1_s1"])


def test_questionable_thematic_support_is_shown_as_not_comparable_with_the_reason_recorded():
    r = compare.apply_pair_guardrail(judgment("supports", "thematic_only"), "pa", "pb", STORED)
    assert r["label"] == "not_comparable" and r["model_label"] == "supports"
    assert r["comparability"] == "thematic_only" and "Thematic similarity is not support" in r["label_note"]
    assert r["explanation"] == "Both improve performance."  # Gemini's explanation is kept, not rewritten


def test_genuinely_comparable_support_is_kept():
    for comp in ("same_hypothesis_same_measure", "same_hypothesis_different_setting"):
        r = compare.apply_pair_guardrail(judgment("supports", comp), "pa", "pb", STORED)
        assert r["label"] == "supports" and r["label_note"] is None
    r = compare.apply_pair_guardrail(judgment("genuine_contradiction", "same_hypothesis_same_measure"), "pa", "pb", STORED)
    assert r["label"] == "genuine_contradiction"


def test_prompts_require_same_hypothesis_and_carry_author_dependence():
    claim = {"paper_title": "T", "text": "c", "conditions": {}, "context": [{"id": "pa:p1_s1", "text": "s"}]}
    p = compare.build_pair_prompt("Q?", claim, claim, {}, dependence="Independence not established — share 4 authors")
    assert "Thematic similarity is NOT support" in p and "absence of a contradiction is not support" in p
    assert "share 4 authors" in p and "Never describe support as independent" in p
    i = compare.build_impact_prompt("Q?", claim, claim, {}, {"label": "supports", "explanation": ""},
                                    dependence="share 4 authors")
    assert "adds independent support" not in i and "never call it independent" in i and "share 4 authors" in i


def _graph(label, front_a, front_b):
    papers = [{"id": "pa", "title": front_a[0]}, {"id": "pb", "title": front_b[0]}]
    page1 = {"pa": [{"id": "pa:p1_s1", "text": front_a[1]}], "pb": [{"id": "pb:p1_s1", "text": front_b[1]}]}
    claims = [{"id": 1, "paper_id": "pa", "text": "A", "sentence_ids": []},
              {"id": 2, "paper_id": "pb", "text": "B", "sentence_ids": []}]
    rels = [{"id": 5, "claim_a": 1, "claim_b": 2, "label": label, "cited_sentence_ids": []}]
    return graph.build_graph("Q?", papers, claims, rels, {}, authors=A.AuthorIndex(papers, page1))


def test_shared_authorship_qualifies_support_but_never_changes_the_label():
    g = _graph("supports", (NRMS_T, NRMS), (MIND_T, MIND))
    rel = g["relationships"]["5"]
    assert rel["label"] == "supports" and rel["independence"]["level"] == "review"
    assert rel["independence"]["headline"] == "Independence not established — review needed"
    edge = next(e for e in g["edges"] if e["data"]["id"] == "rel:5")
    assert edge["data"]["label"] == "supports" and edge["data"]["independence"] == "review"
    assert g["papers"]["pa"]["authors"]["names"][0] == "Chuhan Wu"


def test_unrelated_studies_are_not_flagged_for_independence():
    g = _graph("not_comparable", (NRMS_T, NRMS), (LAMP_T, LAMP))
    assert g["relationships"]["5"]["independence"]["status"] == A.NONE_DETECTED
    assert g["relationships"]["5"]["independence"]["level"] is None


# ---------- chatbot ----------
def _index():
    papers = [{"id": "nrms", "title": NRMS_T}, {"id": "mind", "title": MIND_T}, {"id": "lamp", "title": LAMP_T}]
    return A.AuthorIndex(papers, {"nrms": [{"id": "nrms:p1_s1", "text": NRMS}],
                                  "mind": [{"id": "mind:p1_s1", "text": MIND}],
                                  "lamp": [{"id": "lamp:p1_s1", "text": LAMP}]})


def test_confirmation_between_shared_author_papers_must_acknowledge_it():
    idx = _index()
    bad = "The MIND study [[paper:mind]] confirms this finding from [[paper:nrms]]."
    issues = chat.independence_issues(bad, {"mind", "nrms"}, idx)
    assert issues and issues[0]["kind"] == "shared_authors_unacknowledged"
    ok = bad + " The two papers share authors, so this is related work rather than independent corroboration."
    assert chat.independence_issues(ok, {"mind", "nrms"}, idx) == []
    assert chat.independence_issues("LaMP supports this.", {"lamp", "nrms"}, idx) == []  # no shared authors


def test_independence_is_never_asserted_but_may_be_denied():
    idx = _index()
    assert chat.independence_issues("This was independently replicated.", set(), idx)[0]["kind"] == "independence_claimed"
    assert chat.independence_issues("This is not independent confirmation.", set(), idx) == []
    assert chat.independence_issues("Whether the evidence is independent is unclear.", set(), idx) == []


def test_answers_that_overstate_independence_are_withheld():
    idx = _index()
    shown = {"mind:p6_s10": "Second, among the neural news recommendation methods, NRMS can achieve the best performance."}
    ans = ChatAnswer(answer="[[paper:mind]] confirms [[paper:nrms]].", cited_sentence_ids=["mind:p6_s10"],
                     insufficient_evidence=False)
    out = chat.validate_answer(ans, shown, {"paper:mind", "paper:nrms", "q"},
                               independence=lambda a: chat.independence_issues(a, {"mind", "nrms"}, idx))
    assert out["guardrail"] == chat.GUARDRAIL_INDEPENDENCE and out["cited_sentence_ids"] == []


# ---------- review audit trail (database) ----------
def _seed_relationship(conn):
    for pid in ("pa", "pb"):
        db.insert_paper(conn, {"id": pid, "title": f"Paper {pid}"})
        db.insert_sentences(conn, [{"id": f"{pid}:p1_s1", "paper_id": pid, "page": 1, "idx": 1, "section": "results",
                                    "text": f"{pid} result."},
                                   {"id": f"{pid}:p1_s2", "paper_id": pid, "page": 1, "idx": 2, "section": "results",
                                    "text": f"{pid} second result."}])
    a = db.insert_claim(conn, {"paper_id": "pa", "text": "A", "conditions": {}, "sentence_ids": ["pa:p1_s1"]},
                        [1.0] + [0.0] * 767)
    b = db.insert_claim(conn, {"paper_id": "pb", "text": "B", "conditions": {}, "sentence_ids": ["pb:p1_s1"]},
                        [0.9, 0.1] + [0.0] * 766)
    rid = db.upsert_relationship(conn, {"claim_a": a, "claim_b": b, "label": "supports", "explanation": "x",
                                        "condition_diff": {}, "key_differences": [], "insufficient_evidence": False,
                                        "cited_sentence_ids": ["pa:p1_s1", "pb:p1_s1"]})
    return a, b, rid


def _proposal(rid, a, b, **kw):
    return {"target_kind": "relationship", "target_id": rid, "expected_label": "supports", "expected_claims": [b, a],
            "proposed_label": "not_comparable", "reason": "Different hypotheses.", **kw}


def test_identical_proposals_are_recorded_once_and_changed_evidence_is_a_new_proposal(clean_db):
    with db.connect() as conn:
        a, b, rid = _seed_relationship(conn)
        first = reviews.record_proposals(conn, [_proposal(rid, a, b), _proposal(rid, a, b)], "model", "test", apply=True)
        assert [x["action"] for x in first["actions"]] == ["recorded", "skip_duplicate"]  # same file, once
        again = reviews.record_proposals(conn, [_proposal(rid, a, b)], "model", "test", apply=True)
        assert [x["action"] for x in again["actions"]] == ["skip_duplicate"]               # same fingerprint
        # The relationship is regenerated with different evidence (same id, label, claims): a new proposal.
        db.upsert_relationship(conn, {"claim_a": a, "claim_b": b, "label": "supports", "explanation": "y",
                                      "condition_diff": {}, "key_differences": [], "insufficient_evidence": False,
                                      "cited_sentence_ids": ["pa:p1_s2", "pb:p1_s2"]})
        changed = reviews.record_proposals(conn, [_proposal(rid, a, b)], "model", "test", apply=True)
        assert [x["action"] for x in changed["actions"]] == ["recorded"]
        rows = db.all_reviews(conn)
        assert len(rows) == 2 and rows[0]["evidence"]["target_fingerprint"] != rows[1]["evidence"]["target_fingerprint"]
        assert all(r["status"] == "proposed" and r["reviewer_type"] == "model" for r in rows)
        stored = conn.execute("SELECT label FROM relationships WHERE id = %s", (rid,)).fetchone()
        assert stored["label"] == "supports"  # proposals never change the stored label


def test_reviews_without_a_fingerprint_are_not_duplicated(clean_db):
    with db.connect() as conn:
        a, b, rid = _seed_relationship(conn)
        db.add_review(conn, {"target_kind": "relationship", "target_id": rid, "original_label": "supports",
                             "proposed_label": "not_comparable", "reason": "Different hypotheses.",
                             "reviewer_type": "human", "reviewer": "earlier"})
        out = reviews.record_proposals(conn, [_proposal(rid, a, b)], "model", "test", apply=True)
        assert [x["action"] for x in out["actions"]] == ["skip_unverifiable_existing"]
        assert len(db.all_reviews(conn)) == 1


def test_proposals_for_a_different_target_are_rejected_and_nothing_is_written(clean_db):
    with db.connect() as conn:
        a, b, rid = _seed_relationship(conn)
        out = reviews.record_proposals(conn, [_proposal(rid, a, b), _proposal(rid, a, 999)], "model", "t", apply=True)
        assert out["rejected"] and "claims are" in out["rejected"][0]["problems"][0]
        assert out["actions"] == [] and db.all_reviews(conn) == []
        bad_fp = reviews.record_proposals(conn, [_proposal(rid, a, b, expected_fingerprint="0" * 64)], "model", "t",
                                          apply=True)
        assert "fingerprint differs" in bad_fp["rejected"][0]["problems"][0]


def test_only_a_named_human_decision_closes_a_review_and_reset_clears_reviews(clean_db):
    with db.connect() as conn:
        a, b, rid = _seed_relationship(conn)
        reviews.record_proposals(conn, [_proposal(rid, a, b)], "model", "test", apply=True)
        review_id = db.all_reviews(conn)[0]["id"]
        with pytest.raises(ValueError):
            db.decide_review(conn, review_id, "accepted", "  ")
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.connect() as conn:
            conn.execute("UPDATE relationship_reviews SET status = 'accepted' WHERE id = %s", (review_id,))
    with db.connect() as conn:
        row = db.decide_review(conn, review_id, "accepted", "Dr. Reviewer", "Agreed.")
        assert row["status"] == "accepted" and row["decided_by"] == "Dr. Reviewer"
        assert db.decide_review(conn, review_id, "rejected", "Someone") is None  # already decided
        db.delete_all(conn)
        assert db.all_reviews(conn) == []
