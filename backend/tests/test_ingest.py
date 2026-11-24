import fitz
import pytest

from app import ingest

def make_pdf(pages):
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data

def test_split_handles_abbreviations_and_keeps_short_sentences():
    text = ("Students improved by 25% (e.g. Smith et al. 2020). The effect was large. "
            "Fig. 2 shows the results for all groups. Gains vanished.")
    assert ingest.split_sentences(text) == [
        "Students improved by 25% (e.g. Smith et al. 2020).",
        "The effect was large.",
        "Fig. 2 shows the results for all groups.",
        "Gains vanished.",  # short findings are preserved
    ]

def test_headings_and_page_numbers_removed_but_findings_kept():
    text = "2. Results\nNo effect.\n3.1 Study design\nWe ran a trial with 40 pupils.\n12\nPage 3 of 10\n—"
    assert ingest.split_sentences(text) == ["No effect.", "We ran a trial with 40 pupils."]

def test_short_wrapped_line_mid_sentence_is_not_treated_as_heading():
    text = "Scores rose in\nthe control group\nas well."
    assert ingest.split_sentences(text) == ["Scores rose in the control group as well."]

def test_normalize_ligatures_and_hyphenation():
    assert ingest.normalize_text("signiﬁcant gains in learn-\ning out-\ncomes") == "significant gains in learning outcomes"

def test_sentence_ids_are_globally_unique_across_papers():
    pages = ["The tutor group scored higher on the test. Gains persisted for twelve weeks."]
    a = ingest.build_sentences("paper-a", pages)
    b = ingest.build_sentences("paper-b", pages)
    ids = [s["id"] for s in a + b]
    assert len(ids) == len(set(ids))
    assert a[0]["id"] == "paper-a:p1_s1" and b[0]["id"] == "paper-b:p1_s1"
    assert all(s["paper_id"] == "paper-a" for s in a)

def test_invalid_paper_id_rejected():
    with pytest.raises(ValueError):
        ingest.build_sentences("Paper:A", ["Some sentence with enough words here."])

def test_pdf_extraction_stops_at_references():
    pdf = make_pdf([
        "Results\nThe tutor group scored 25 percent higher after twelve weeks.\n"
        "This difference was statistically significant.",
        "Discussion of the results continues here for a while.\nReferences\nSmith J. A cited paper title here. 2020.",
        "Appendix text that should never be indexed at all.",
    ])
    pages = ingest.extract_pages(pdf)
    assert len(pages) == 2
    sents = ingest.build_sentences("p1", pages)
    texts = " ".join(s["text"] for s in sents)
    assert "25 percent higher" in texts
    assert "cited paper" not in texts and "Appendix" not in texts
    assert sents[0]["id"] == "p1:p1_s1" and sents[-1]["page"] == 2

def test_sentences_tagged_with_section():
    pages = ["Abstract\nWe studied tutors.\n1. Introduction\nTutors are common.",
             "2. Methods\n2.1 Participants\nWe enrolled 40 pupils.\n3. Results\nScores rose.\n4. Conclusion\nTutors helped."]
    s = ingest.build_sentences("p1", pages)
    assert [(x["section"], x["text"]) for x in s] == [
        ("abstract", "We studied tutors."), ("introduction", "Tutors are common."),
        ("methods", "We enrolled 40 pupils."), ("results", "Scores rose."), ("conclusion", "Tutors helped."),
    ]
    assert [x["id"] for x in s] == ["p1:p1_s1", "p1:p1_s2", "p1:p2_s1", "p1:p2_s2", "p1:p2_s3"]

def _title_pdf(meta_title=None, stamp=True):
    doc = fitz.open()
    page = doc.new_page()
    if stamp:  # arXiv-style sideways identifier stamp
        page.insert_text((20, 600), "arXiv:2206.14145v1 [cs.CL] 28 Jun 2022", fontsize=20, rotate=90)
    page.insert_text((72, 90), "Question Personalization in an Intelligent", fontsize=17)
    page.insert_text((72, 112), "Tutoring System", fontsize=17)
    page.insert_text((72, 140), "Sabina Elkins, Robert Belfer, and Jackie Cheung", fontsize=10)
    page.insert_text((72, 170), "Abstract. We study how question phrasing affects learning outcomes.", fontsize=9)
    if meta_title is not None:
        doc.set_metadata({"title": meta_title})
    data = doc.tobytes()
    doc.close()
    return data

def test_guess_title_joins_wrapped_large_font_title_and_ignores_arxiv_stamp():
    assert ingest.guess_title(_title_pdf()) == "Question Personalization in an Intelligent Tutoring System"

def test_guess_title_prefers_real_metadata_title():
    pdf = _title_pdf(meta_title="Evaluating a Data-Driven Redesign Process for Intelligent Tutoring Systems")
    assert ingest.guess_title(pdf) == "Evaluating a Data-Driven Redesign Process for Intelligent Tutoring Systems"

@pytest.mark.parametrize("junk", ["arXiv:2206.14145v1", "Microsoft Word - draft_v3.docx", "paper.pdf", "main", ""])
def test_guess_title_ignores_junk_metadata(junk):
    assert ingest.guess_title(_title_pdf(meta_title=junk)) == "Question Personalization in an Intelligent Tutoring System"

def test_pdf_without_text_raises_clear_error():
    blank = make_pdf(["", ""])  # e.g. a scanned PDF: pages but no text layer
    with pytest.raises(ingest.NoExtractableTextError, match="No extractable text"):
        ingest.extract_pages(blank)

def test_only_noise_raises_clear_error():
    with pytest.raises(ingest.NoExtractableTextError):
        ingest.build_sentences("p1", ["12\nResults\n—"])

def test_non_pdf_bytes_raise_clear_error():
    with pytest.raises(ingest.NoExtractableTextError, match="Could not open"):
        ingest.extract_pages(b"this is not a pdf")