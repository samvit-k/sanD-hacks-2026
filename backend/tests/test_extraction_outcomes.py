"""Zero-claim diagnosis: honest, persisted extraction outcomes; section detection for experiment papers;
flattened tables tagged in the prompt. Citation validation is unchanged (see test_validation.py)."""
from app import extract, ingest
from app.contracts import Conditions, ExtractedClaim, ExtractionResult

from .test_workspace import api, upload  # noqa: F401  (fixture)

STORED = {"pa:p1_s1": "pa", "pa:p1_s2": "pa", "pb:p1_s1": "pb"}


def claim(text, ids):
    return ExtractedClaim(text=text, sentence_ids=ids, conditions=Conditions())


# ---------- outcome classification ----------
def test_no_candidates_is_reported_as_no_relevant_findings_with_the_models_note():
    raw = ExtractionResult(claims=[], no_findings_note="The paper studies news recommendation, not GraphRAG.")
    kept, report = extract.validate_claims(raw, "pa", STORED, 3)
    assert kept == []
    assert report["outcome"] == extract.OUTCOME_NONE_RETURNED and report["returned"] == 0
    assert report["model_note"] == "The paper studies news recommendation, not GraphRAG."


def test_candidates_that_all_fail_verification_are_reported_as_rejected_not_as_no_findings():
    raw = ExtractionResult(claims=[claim("A", ["pb:p1_s1"]), claim("B", ["pa:p9_s9"])])
    kept, report = extract.validate_claims(raw, "pa", STORED, 3)
    assert kept == [] and report["outcome"] == extract.OUTCOME_ALL_REJECTED
    assert report["returned"] == 2 and report["rejected_invalid_citations"] == 2


def test_a_note_is_dropped_when_claims_were_kept_and_is_length_limited():
    raw = ExtractionResult(claims=[claim("Good.", ["pa:p1_s1"])], no_findings_note="Only one finding.")
    _, report = extract.validate_claims(raw, "pa", STORED, 3)
    assert report["outcome"] == extract.OUTCOME_CLAIMS and report["model_note"] is None
    _, report = extract.validate_claims(ExtractionResult(claims=[], no_findings_note="x " * 500), "pa", STORED, 3)
    assert len(report["model_note"]) <= extract.MAX_NOTE_LEN


# ---------- persisted and shown, never evidence ----------
UNRELATED = ("News recommendation study",
             "Abstract\nWe study click prediction for news articles.\nDiscussion\nClick models matter for platforms.")


def test_zero_claim_upload_is_persisted_with_its_outcome_and_an_honest_warning(api):
    c, _ = api
    r = upload(c, UNRELATED[1], UNRELATED[0], mode="library")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims_kept"] == 0
    assert body["extraction"]["outcome"] == "no_relevant_findings"
    assert body["warning"].startswith("No relevant findings identified for this question")
    paper = c.get("/api/graph").json()["papers"][body["paper"]["id"]]
    assert paper["extraction"]["outcome"] == "no_relevant_findings"
    assert paper["extraction"]["question"] == c.get("/api/workspace").json()["question"]
    graph = c.get("/api/graph").json()
    assert graph["claims"] == {} and graph["relationships"] == {}  # nothing is invented to fill the graph
    assert c.get("/api/checks").json()["ok"]


def test_successful_extraction_records_claims_extracted(api):
    c, _ = api
    r = upload(c, "Abstract\nA trial.\nResults\nScores rose 25 percent after 12 weeks.\n", "Trial", mode="library")
    assert r.json()["extraction"]["outcome"] == "claims_extracted" and r.json()["warning"] is None


# ---------- section detection ----------
NRMS_LIKE = [
    "Abstract\nWe propose an approach.\n1 Introduction\nNews matters a lot.\n",
    "3 Experiments\n3.1 Datasets and Experimental Settings\nWe used one month of logs.\n"
    "3.2 Performance Evaluation\nWe compare against baselines.\nMethods\nAUC MRR\n"
    "Third, our approach performs better than all baseline methods.\n",
    "4 Conclusion\nThe approach works well.\nLimitations\nOnly one dataset was used.\n",
]


def test_experiment_headings_map_to_results_and_table_headers_do_not_switch_sections():
    sents = {s["text"]: s["section"] for s in ingest.build_sentences("nrms", NRMS_LIKE)}
    assert sents["We used one month of logs."] == "methods"           # "Datasets and Experimental Settings"
    assert sents["We compare against baselines."] == "results"         # "Performance Evaluation"
    after_table = [sec for text, sec in sents.items() if "performs better than all baseline methods" in text]
    assert after_table == ["results"]  # the table header cell "Methods" did not switch the section
    assert sents["The approach works well."] == "conclusion"
    assert sents["Only one dataset was used."] == "limitations"         # unnumbered back matter still honoured


# PyMuPDF's real layout for NRMS/MIND: the heading number on its own line, table cells one per line.
SPLIT_LAYOUT = [
    "Abstract\nWe propose an approach.\n1\nIntroduction\nNews matters a lot.\n",
    "3\nExperiments\n5.1\nExperimental Settings\nWe used one month of logs.\n5.2\nPerformance Comparison\n"
    "Methods\nAUC\n0.56\nMethods\n59.93\n3602\nDataset\nThird, our approach performs better than all baselines.\n",
    "3603\nPage text continues here and is not a heading.\n4\nConclusion\nThe approach works well.\n",
]


def test_split_heading_numbers_are_rejoined_and_subsections_return_to_their_parent():
    sents = {s["text"]: s["section"] for s in ingest.build_sentences("mind", SPLIT_LAYOUT)}
    assert sents["News matters a lot."] == "introduction"
    assert sents["We used one month of logs."] == "methods"        # "5.1 Experimental Settings"
    after = [sec for text, sec in sents.items() if "performs better than all baselines" in text]
    assert after == ["results"]  # "5.2 Performance Comparison" -> parent "3 Experiments"; table cells never switch
    assert sents["The approach works well."] == "conclusion"
    # Page numbers and table values are not joined into headings, and body text is kept verbatim.
    assert any(t.startswith("Page text continues here") for t in sents)
    assert not any("Performance Comparison" in t or "Experimental Settings" in t for t in sents)


def test_unnumbered_papers_keep_using_plain_headings():
    sents = {s["text"]: s["section"] for s in ingest.build_sentences("p", ["Methods\nWe ran a trial.\nResults\n"
                                                                            "Scores rose 25 percent.\n"])}
    assert sents["We ran a trial."] == "methods" and sents["Scores rose 25 percent."] == "results"


# ---------- flattened tables ----------
TABLE = "LibFM 0.5661 0.2414 0.2689 0.3552 DSSM 0.5949 0.2675 0.2881 0.3800 NRMS* 0.6275 0.2985 0.3217 0.4139"


def test_flattened_tables_are_tagged_in_the_prompt_but_quoted_verbatim():
    assert extract.looks_like_table(TABLE)
    assert not extract.looks_like_table("Third, our approach performs better than all baseline methods.")
    sentences = [{"id": "nrms:p4_s18", "section": "methods", "text": TABLE},
                 {"id": "nrms:p4_s23", "section": "results", "text": "Our approach performs better than all baselines."}]
    prompt = extract.build_extraction_prompt("Q?", "NRMS", sentences, 3, 1200)
    assert f"[nrms:p4_s18] (table) {TABLE}" in prompt  # the text itself is unchanged
    assert "[nrms:p4_s23] (results)" in prompt
    assert "Lines marked (table) are flattened table text" in prompt
    assert "do not stretch unrelated results to fit it" in prompt
