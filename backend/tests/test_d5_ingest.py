"""Stage D5: block-aware PDF extraction (new uploads only) and conservative heading recognition.

The PDFs are synthetic, built with PyMuPDF to reproduce the layouts that produced malformed quotations in the D4
papers (NRMS, LaMP, MIND): a caption, a footnote or a table inserted between the two halves of a hyphenated body
sentence. The passages are short excerpts of the real sentences. Content-stream order = insertion order, as in the
real files. PyMuPDF's built-in Helvetica cannot encode curly quotes, so the fixtures use straight ones (the real
quotations' curly quotes are unaffected). The generator asserts that every block was inserted, that no blocks overlap, that every word is in the
PDF's text layer, and that each intended block is a separate PDF text block.
"""
import fitz
import pytest

from app import ingest

COLUMNS = {"L": 60, "R": 320}
WIDTH, GAP, TOP, BOTTOM = 230, 14, 50, 822
BODY_FILL = ("News recommendation is an important technique for personalized news service. Compared with product "
             "and movie recommendations which have been comprehensively studied, the research on news recommendation "
             "is much more limited, mainly due to the lack of a high-quality benchmark dataset.")

def make_pdf(pages):
    """pages: list of items (column, fontsize, text[, top]) in content-stream order; one textbox per item.
    Without `top`, an item is placed below the previous item of its column using the height actually used."""
    doc = fitz.open()
    for items in pages:
        page = doc.new_page(width=595, height=842)
        cursor = {c: TOP for c in COLUMNS}
        placed = []
        for col, size, text, *top in items:
            y = top[0] if top else cursor[col]
            rect = fitz.Rect(COLUMNS[col], y, COLUMNS[col] + WIDTH, BOTTOM)
            unused = page.insert_textbox(rect, text, fontsize=size, fontname="helv")
            assert unused >= 0, f"textbox did not fit: {text[:40]!r}"
            used = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y1 - unused)
            assert not any(used.intersects(p) for p in placed), f"blocks overlap: {text[:40]!r}"
            placed.append(used)
            cursor[col] = used.y1 + GAP
    pdf = doc.tobytes()
    with fitz.open(stream=pdf, filetype="pdf") as check:
        for page, items in zip(check, pages):
            layer = " ".join(page.get_text("text").split())
            for _, _, text, *_ in items:
                missing = [w for w in text.split() if w not in layer]
                assert not missing, f"text missing from the PDF: {missing[:5]}"
            blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
            assert len(blocks) == len(items), f"expected {len(items)} separate text blocks, found {len(blocks)}"
    return pdf

def sentences(pdf):
    return ingest.build_sentences("p", ingest.extract_page_blocks(pdf))

def find(sents, needle):
    return [s for s in sents if needle in s["text"]]

def test_the_fixture_generator_refuses_text_that_does_not_fit():
    with pytest.raises(AssertionError, match="did not fit"):
        make_pdf([[("L", 11, "word " * 2000)]])

# The caption sits in the right column but comes between the two halves of the body sentence in the content stream.
MIND_PAGE = [
    ("L", 11, BODY_FILL),
    ("L", 11, "We find that by replacing the origi-"),
    ("R", 10, "Table 5: News representation with different news information. \"Abs.\", \"Cat.\" and \"Ent.\" mean "
              "abstract, category and entity, respectively."),
    ("L", 11, "nal word embedding module with the pre-trained BERT model, the performance of different news "
              "recommendation methods can be improved."),
]

def test_a_caption_between_the_halves_of_a_sentence_no_longer_breaks_it():
    pdf = make_pdf([MIND_PAGE])
    old = " ".join(ingest.build_sentences("p", ingest.extract_pages(pdf))[i]["text"] for i in range(3))
    s = sentences(pdf)
    body = find(s, "original word embedding module")
    assert len(body) == 1 and body[0]["kind"] == "body" and "Abs." not in body[0]["text"]
    assert body[0]["text_check"] == ingest.JOINED_BLOCKS  # reconstructed across blocks: flagged, not hidden
    caption = find(s, "Table 5: News representation")
    assert caption and caption[0]["kind"] == "caption" and caption[0]["text_check"] == ingest.SINGLE_BLOCK
    assert "original word" not in old  # the line-based extraction did break this sentence (artifact reproduced)

LAMP_PAGE = [
    ("L", 11, BODY_FILL),
    ("L", 11, "The results show that, except for the Personalized Tweet Paraphrasing task, using the user's "
              "profile with LLMs im-"),
    ("R", 9, "5As previously stated, FiD approach cannot be utilized with untrained models.", 770),
    ("L", 11, "proves performance on the benchmark."),
]

def test_a_footnote_is_separated_from_the_sentence_it_interrupted():
    pdf = make_pdf([LAMP_PAGE])
    assert any("im5As" in x["text"].replace(" ", "") or "im- 5As" in x["text"]
               for x in ingest.build_sentences("p", ingest.extract_pages(pdf)))  # artifact reproduced
    s = sentences(pdf)
    body = find(s, "using the user's profile with LLMs improves performance")
    assert len(body) == 1 and "5As" not in body[0]["text"]
    note = find(s, "As previously stated")
    assert note and note[0]["kind"] == "footnote"

NRMS_PAGE = [
    ("L", 11, BODY_FILL),
    ("L", 11, "First, neural recommendation methods such as DSSM and NRMS outperform traditional recom-"),
    ("R", 7, "AUC\nMRR\nnDCG@5\nnDCG@10\nLibFM\n0.5661\n0.2414\n0.2689\n0.3552\nNRMS*\n0.6275\n0.2985\n0.3217\n0.4139"),
    ("R", 10, "Table 2: The results of different methods. *The improvement is significant at p < 0.01."),
    ("L", 11, "mendation methods such as LibFM on news recommendation."),
]

def test_tables_and_captions_become_their_own_labelled_sentences():
    pdf = make_pdf([NRMS_PAGE])
    assert any("0.6275" in x["text"] and "outperform" in x["text"]
               for x in ingest.build_sentences("p", ingest.extract_pages(pdf)))  # artifact reproduced
    s = sentences(pdf)
    body = find(s, "First, neural recommendation methods")
    assert len(body) == 1 and body[0]["text"].endswith("on news recommendation.")
    assert "0.6275" not in body[0]["text"] and "Table 2" not in body[0]["text"]
    table = find(s, "0.6275")
    assert len(table) == 1 and table[0]["kind"] == "table" and "0.4139" in table[0]["text"]  # one block, verbatim
    caption = find(s, "The improvement is significant at p < 0.01.")
    assert caption and caption[0]["kind"] == "caption"

def test_smaller_font_prose_such_as_an_abstract_stays_body_text():
    abstract = ("This paper highlights the importance of personalization in large language models and introduces "
                "the LaMP benchmark for training and evaluating language models.")
    s = sentences(make_pdf([[("L", 11, BODY_FILL), ("L", 10, abstract), ("L", 11, BODY_FILL)]]))
    assert find(s, "This paper highlights the importance")[0]["kind"] == "body"

def test_sentence_ids_stay_unique_and_page_numbered():
    s = sentences(make_pdf([MIND_PAGE, LAMP_PAGE]))
    ids = [x["id"] for x in s]
    assert len(ids) == len(set(ids)) and all(x["id"] == f"p:p{x['page']}_s{x['idx']}" for x in s)
    assert {x["page"] for x in s} == {1, 2}

def test_plain_text_pages_keep_the_original_behaviour():
    s = ingest.build_sentences("p", ["Methods\nWe ran a trial.\nResults\nScores rose 25 percent.\n"])
    assert [(x["section"], x["text"]) for x in s] == [("methods", "We ran a trial."), ("results", "Scores rose 25 percent.")]
    assert "kind" not in s[0] and "text_check" not in s[0]

# ---------- headings ----------
def heading_pdf(texts):
    return make_pdf([[("L", 11, t) for t in texts]])

def sections(texts):
    return {x["text"]: x["section"] for x in sentences(heading_pdf(texts))}

def section_of(sec, ending):
    """Section of the sentence ending with `ending` (a rejected heading stays in the text flow, so it may prefix it)."""
    hits = [v for k, v in sec.items() if k.endswith(ending)]
    assert len(hits) == 1, (ending, sec)
    return hits[0]

def test_a_sequential_approach_heading_is_methods_and_its_subsections_follow_it():
    sec = sections(["1\nIntroduction", "News matters a lot.", "2\nOur Approach", "Our model has three modules.",
                    "2.1\nNews Encoder", "The news encoder learns representations.",
                    "3\nExperiments", "Our approach performs better than all baselines."])
    assert sec["News matters a lot."] == "introduction"
    assert sec["Our model has three modules."] == "methods"                # NRMS "2 Our Approach"
    assert sec["The news encoder learns representations."] == "methods"    # sub-heading inherits its parent
    assert sec["Our approach performs better than all baselines."] == "results"
    assert "Our Approach" not in " ".join(sec)                             # the heading is not a sentence

def test_other_sequential_keyword_less_headings_are_unclassified_not_guessed():
    sec = sections(["1\nIntroduction", "Personalization matters.", "2\nThe LaMP Benchmark", "The benchmark has seven tasks.",
                    "3\nExperiments", "Fine-tuning improves results."])
    assert sec["The benchmark has seven tasks."] == ingest.UNCLASSIFIED    # LaMP "2 The LaMP Benchmark"
    assert sec["Fine-tuning improves results."] == "results"

@pytest.mark.parametrize("decoy", ["7\nOur Approach",            # out of sequence (expected 2): e.g. a page number
                                   "(2)\nModel Training",          # equation number
                                   "2\nresults improved a lot.",   # body text, not title-like
                                   "0.62\nModel Training"])        # table value
def test_page_numbers_equation_numbers_table_values_and_body_text_are_not_headings(decoy):
    sec = sections(["1\nIntroduction", "News matters a lot.", decoy, "Text after the decoy."])
    assert section_of(sec, "Text after the decoy.") == "introduction"

def test_a_late_bare_abstract_label_inside_a_figure_does_not_switch_sections():
    sec = sections(["Abstract", "We study news.", "1\nIntroduction", "News matters a lot.",
                    "Abstract\nMike Tomlin has admitted that the Steelers played a role.", "Back in the introduction."])
    assert sec["We study news."] == "abstract" and sec["Back in the introduction."] == "introduction"

# ---------- display-only artifact flags for stored (pre-D5) quotations ----------
def test_known_malformed_d4_quotations_are_flagged_for_verification():
    assert ingest.layout_artifacts("“Abs.”, “Cat.” and “Ent.” mean abstract, category and entity, respectively. nal "
                                   "word embedding module with the pre-trained BERT model.") == ["word_fragment"]
    assert "footnote_glued" in ingest.layout_artifacts("using the user’s profile with LLMs im5As previously stated")
    nrms = ("First, neural recommendation methods outperform traditional methods on news recAUC MRR nDCG@5 nDCG@10 "
            "LibFM 0.5661 0.2414 0.2689 0.3552 DSSM 0.5949 0.2675 0.2881 0.3800 Table 2: The results of different "
            "methods. *The improvement is significant at p < 0.01. ommendation.")
    assert {"caption_inside", "table_inside_prose"} <= set(ingest.layout_artifacts(nrms))
    flattened = ("Experiment Group Solution Acceptance* Ultimate Failure Rate* Skip Rate n Expected 0.626 ± 0.069 0.163 ± "
                 "0.053 0.105 ± 0.044 190 Non-Expected 0.468 ± 0.083 0.295 ± 0.076 0.144 ± 0.058 139")
    assert ingest.layout_artifacts(flattened) == ["table_inside_prose"]
    for clean in ("Third, our approach performs better than all baseline methods.",
                  "We use e.g. bert and roberta models.", "Results in Table 2 show gains of 5%.",
                  # real prose reporting statistics (original project, p6_s7) is not table text
                  "Both conditions exhibited comparable improvements: students using the Original Tutor increased from "
                  "M = 0.41 (SD = 0.34) to M = 0.63 (SD = 0.34), corresponding to a mean gain of 0.21 (SD = 0.38, "
                  "d = 0.57), while students using the Redesigned Tutor improved from M = 0.40 (SD = 0.31)."):
        assert ingest.layout_artifacts(clean) == []