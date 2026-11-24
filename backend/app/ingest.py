"""PDF -> pages -> sentences with globally unique IDs '{paper_id}:p{page}_s{n}'.

Quotes shown anywhere in SpiderMonkey are rebuilt from these stored sentences,
so they are verbatim (after whitespace/ligature normalization) by construction.

Short sentences are kept: brief statements ("Gains did not persist.") can be key
findings. Only clear extraction noise is removed: page numbers, running section
headings, and fragments that contain no words.
"""
import re
import unicodedata
from typing import Dict, List

import fitz  # PyMuPDF

class NoExtractableTextError(ValueError):
    """Raised when a PDF yields no usable text (e.g. a scanned image without OCR)."""

NO_TEXT_MESSAGE = (
    "No extractable text found in this PDF. It may be a scanned image (OCR is not supported "
    "in the MVP). Upload a text-based PDF, or add the paper from its abstract instead."
)

_STOP_HEADING = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|works cited|literature cited)\s*$", re.I)
_SECTION_WORDS = (
    r"abstract|introduction|background|related work|methods?|methodology|materials and methods|"
    r"participants|procedure|results?|findings|experiments?|discussion|conclusions?|limitations|"
    r"acknowledge?ments?|funding|keywords?|appendix"
)
# Lines that are headings or layout noise, removed before sentence splitting.
_NOISE_LINE = [
    re.compile(rf"^\s*(\d+(\.\d+)*\.?\s*)?({_SECTION_WORDS})\s*:?\s*$", re.I),  # "2. Methods", "Results"
    re.compile(r"^\s*\d+(\.\d+)*\.?\s+[A-Z][^.!?]{0,60}$"),                       # "3.1 Study design"
    re.compile(r"^\s*(page\s*)?\d+(\s*(of|/)\s*\d+)?\s*$", re.I),                 # "12", "Page 3 of 10"
    re.compile(r"^[^A-Za-z0-9]*$"),                                                 # "—", "* * *"
]
# Split after . ! ? (optionally followed by a closing quote/bracket) when the next sentence starts plausibly.
_SPLIT = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"”’)\]]))\s+(?=[A-Z0-9\"“(\[])")
_ABBREV_END = re.compile(
    r"(\b(e\.g|i\.e|et al|etc|vs|cf|fig|figs|tab|eq|eqs|no|vol|pp|approx|dr|mr|ms|prof|inc|ltd|st|ca|resp)\.|\b[A-Z]\.)$",
    re.I,
)
_HAS_WORD = re.compile(r"[A-Za-z]{2,}")
_PAPER_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")

def sentence_id(paper_id: str, page: int, idx: int) -> str:
    return f"{paper_id}:p{page}_s{idx}"

def slugify(text: str, max_len: int = 40) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s[:max_len].strip("-")) or "paper"

def check_paper_id(paper_id: str) -> str:
    if not _PAPER_ID.match(paper_id):
        raise ValueError(f"paper_id must match [a-z0-9][a-z0-9-]*, got {paper_id!r}")
    return paper_id

def is_noise_line(line: str) -> bool:
    return any(p.match(line) for p in _NOISE_LINE)

def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)  # ligatures (ﬁ -> fi), full-width chars
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def split_sentences(text: str) -> List[str]:
    """Split text into sentences. Removes heading/noise lines; keeps short sentences."""
    lines = [ln for ln in text.splitlines() if not is_noise_line(ln)]
    parts = [p.strip() for p in _SPLIT.split(normalize_text("\n".join(lines))) if p.strip()]
    merged: List[str] = []
    for part in parts:
        if merged and _ABBREV_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [s for s in merged if _HAS_WORD.search(s)]  # drop fragments with no words ("12", "—")

# ---------- block-aware extraction (ingest_version 2; new uploads only) ----------
INGEST_VERSION = 2
BODY, CAPTION, TABLE, FOOTNOTE = "body", "caption", "table", "footnote"
# Text checks recorded per sentence (ingest_version 2):
SINGLE_BLOCK = "single_block"    # the sentence lies inside ONE text block of the PDF: text-layer order, only
                                 # normalized (ligatures, line-break hyphens, whitespace)
JOINED_BLOCKS = "joined_blocks"  # reconstructed across several body blocks (column/page breaks, or around a
                                 # caption, table or footnote that interrupted the paragraph): verify on the page
_CAPTION = re.compile(r"^\s*(Table|Figure|Fig\.)\s*\d+\s*[:.]", re.I)
_FOOTNOTE_START = re.compile(r"^\s*[\d∗*†‡§¶]")
_NUMERIC_TOKEN = re.compile(r"^[\d.,%±+\-−–()]+$")
SMALL_FONT_DELTA = 0.6      # points below the body font size that mark non-body text
FOOTNOTE_ZONE = 0.65        # footnotes start in the bottom 35% of the page
MIN_TABLE_TOKENS = 6

def _block_text(block: Dict) -> str:
    return "\n".join("".join(s["text"] for s in line["spans"]) for line in block.get("lines", [])).strip()

def _block_font(block: Dict) -> float:
    spans = [s for line in block.get("lines", []) for s in line["spans"] if s["text"].strip()]
    chars = sum(len(s["text"].strip()) for s in spans)
    return sum(s["size"] * len(s["text"].strip()) for s in spans) / chars if chars else 0.0

def classify_block(text: str, font: float, body_font: float, top: float, page_height: float) -> str:
    """body | caption | table | footnote, from position, font size and content (conservative: body by default)."""
    tokens = text.split()
    numeric = sum(bool(_NUMERIC_TOKEN.match(t)) for t in tokens) / max(len(tokens), 1)
    prose = len(tokens) >= 12 and numeric < 0.25 and "." in text
    if _CAPTION.match(text):
        return CAPTION
    small = font < body_font - SMALL_FONT_DELTA
    if small and top > FOOTNOTE_ZONE * page_height and _FOOTNOTE_START.match(text):
        return FOOTNOTE
    if small:
        return BODY if prose else TABLE   # e.g. a 10pt abstract is prose; 7pt cells and figure labels are not
    if numeric >= 0.5 and len(tokens) >= MIN_TABLE_TOKENS:
        return TABLE
    return BODY

def extract_page_blocks(pdf_bytes: bytes) -> List[List[Dict]]:
    """Per page, the text blocks in PDF order, each {"kind", "text"}; stops at a References heading.
    Captions, tables/figure text and footnotes are separated from body text so they no longer interrupt sentences."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # corrupt / not a PDF
        raise NoExtractableTextError(f"Could not open the file as a PDF: {exc}") from exc
    with doc:
        sizes: Dict[float, int] = {}
        raw = []
        for page in doc:
            blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
            raw.append((page.rect.height, blocks))
            for b in blocks:
                for line in b["lines"]:
                    for s in line["spans"]:
                        k = round(s["size"], 1)
                        sizes[k] = sizes.get(k, 0) + len(s["text"].strip())
    body_font = max(sizes, key=sizes.get) if sizes else 0.0
    pages: List[List[Dict]] = []
    for height, blocks in raw:
        kept: List[Dict] = []
        stop = False
        for b in blocks:
            text = _block_text(b)
            lines = text.splitlines()
            cut = next((i for i, line in enumerate(lines) if _STOP_HEADING.match(line)), None)
            if cut is not None:
                text, stop = "\n".join(lines[:cut]).strip(), True
            if text:
                kept.append({"kind": classify_block(text, _block_font(b), body_font, b["bbox"][1], height),
                             "text": text})
            if stop:
                break
        pages.append(kept)
        if stop:
            break
    if not any(b["text"].strip() for p in pages for b in p):
        raise NoExtractableTextError(NO_TEXT_MESSAGE)
    return pages

def _clean_block(text: str) -> str:
    return normalize_text("\n".join(ln for ln in text.splitlines() if not is_noise_line(ln)))

def extract_pages(pdf_bytes: bytes) -> List[str]:
    """Return text per page (1-based order), stopping at a References/Bibliography heading."""
    pages: List[str] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # corrupt / not a PDF
        raise NoExtractableTextError(f"Could not open the file as a PDF: {exc}") from exc
    with doc:
        for page in doc:
            kept: List[str] = []
            stop = False
            for line in page.get_text("text").splitlines():
                if _STOP_HEADING.match(line):
                    stop = True
                    break
                kept.append(line)
            pages.append("\n".join(kept))
            if stop:
                break
    if not any(p.strip() for p in pages):
        raise NoExtractableTextError(NO_TEXT_MESSAGE)
    return pages

_SECTION_CANON = [
    # Setup-style experiment headings are methods ("3.1 Datasets and Experimental Settings") — checked first.
    (re.compile(r"\b(experimental (settings?|setup)|settings?|setup|datasets?|implementation( details)?)\b", re.I),
     "methods"),
    (re.compile(r"\b(results?|findings|experiments?|performance evaluation|evaluation|empirical study)\b", re.I),
     "results"),
    (re.compile(r"\bconclusions?\b", re.I), "conclusion"),
    (re.compile(r"\bdiscussion\b", re.I), "discussion"),
    (re.compile(r"\babstract\b", re.I), "abstract"),
    (re.compile(r"\blimitations\b", re.I), "limitations"),
    (re.compile(r"\b(methods?|methodology|materials|participants|procedure)\b", re.I), "methods"),
    (re.compile(r"\b(introduction|background|related work)\b", re.I), "introduction"),
    (re.compile(r"\b(acknowledge?ments?|funding|appendix)\b", re.I), "other"),
]

_NUMBERED = re.compile(r"^\s*\d+(\.\d+)*\.?\s+\S")
# A section number alone on a line: "3", "5.2", "5.3.1". Not table values ("0.518", "59.93") or page numbers ("3602").
_SECTION_NUMBER = re.compile(r"^\s*[1-9]\d?(\.\d){0,3}\.?\s*$")
_SUBSECTION = re.compile(r"^\s*[1-9]\d?(\.\d){1,3}\.?\s+[A-Z]")  # "5.2 Performance Comparison"
MAX_HEADING_WORDS = 6

def _join_split_headings(lines: List[str]) -> List[str]:
    """PyMuPDF often puts a heading's number on its own line ("3" / "Experiments"). Rejoin them ("3 Experiments")
    so numbered-heading detection sees them. Joined only when the next line is short and the result is heading-shaped,
    and, for a bare top-level number (which may be a page number), only when it names a known section, so body text
    is never swallowed. Otherwise both lines are kept unchanged."""
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is not None and _SECTION_NUMBER.match(line) and len(nxt.split()) <= MAX_HEADING_WORDS:
            joined = f"{line.strip()} {nxt.strip()}"
            if heading_section(joined) or (_SUBSECTION.match(joined) and _NOISE_LINE[1].match(joined)):
                out.append(joined)
                i += 2
                continue
        out.append(line)
        i += 1
    return out

def _is_subsection_heading(line: str) -> bool:
    return bool(_SUBSECTION.match(line) and _NOISE_LINE[1].match(line))

# Keyword-less numbered top-level headings ("2 Our Approach") — block-aware extraction only. Accepted only when the
# PDF block holds nothing but the number and a short title-like line, AND the number is exactly the previous
# top-level number + 1. Page numbers, equation numbers, table values and body text fail at least one test.
UNCLASSIFIED = "unclassified"
# Words that make a sequential keyword-less TOP-LEVEL heading a methods section ("2 Our Approach"). Applied only to
# such headings, never to sub-headings ("5.3.1 News Representation Model" sits inside Experiments).
_TOP_LEVEL_METHODS = re.compile(r"\b(approach|model|models|framework|architecture|proposed method)\b", re.I)
_TOP_NUMBER = re.compile(r"^\s*([1-9]\d?)\.?\s*$")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "vs", "via",
                "with"}

def _title_like(text: str) -> bool:
    words = text.split()
    if not 1 <= len(words) <= MAX_HEADING_WORDS or text.rstrip()[-1:] in ".:;,!?" or not words[0][:1].isupper():
        return False
    if not any(re.match(r"^[A-Za-z]{3,}", w) for w in words):
        return False
    return all(w[:1].isupper() or w.lower() in _SMALL_WORDS or not w[:1].isalpha() for w in words)

def standalone_top_heading(block_text: str, last_top: int):
    """(number, heading) if a body block is only a sequential top-level heading, else None."""
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    if not 2 <= len(lines) <= 3:
        return None
    m = _TOP_NUMBER.match(lines[0])
    title = " ".join(lines[1:])
    if not m or int(m.group(1)) != last_top + 1 or not _title_like(title):
        return None
    return int(m.group(1)), title

# Unnumbered headings still honoured after numbered ones appear (front/back matter of e.g. ACL papers).
# ("Abstract" is not among them: an abstract always precedes the numbered sections, so a later bare "Abstract" is a
# label inside a figure or table, e.g. MIND's example news article.)
_UNNUMBERED_OK = {"limitations", "other"}

def heading_section(line: str):
    """Canonical section name if `line` is a section heading, else None."""
    if not (_NOISE_LINE[0].match(line) or _NOISE_LINE[1].match(line)):
        return None
    for pattern, name in _SECTION_CANON:
        if pattern.search(line):
            return name
    return None  # a sub-heading like "3.1 Study design": keep the current section

def build_sentences(paper_id: str, pages: List) -> List[Dict]:
    """Sentences with IDs, page numbers and the section they appear in ('front' before any heading).

    `pages` holds either plain page strings (original line-based extraction; abstracts) or, for ingest_version 2,
    lists of blocks from extract_page_blocks. With blocks, body blocks flow together (headings, sections and
    line-break hyphens work across them) while caption, table and footnote blocks become their own sentences,
    labelled `kind`; every block-based sentence also records `text_check` (single_block / joined_blocks)."""
    check_paper_id(paper_id)
    out: List[Dict] = []
    section = "front"
    parent = None  # section of the last top-level numbered heading ("5 Experiments" -> results)
    numbered_seen = False
    last_top = 0   # number of the last top-level numbered heading (block-aware extraction)
    for page_no, page in enumerate(pages, start=1):
        blocked = not isinstance(page, str)
        blocks = page if blocked else [{"kind": BODY, "text": page}]
        body_norms = [_clean_block(b["text"]) for b in blocks if b["kind"] == BODY] if blocked else []
        segments, buf, extras = [], [], []
        # Blocks in PDF order: non-body blocks inherit the section in force where they appear.
        for b in blocks:
            if b["kind"] != BODY:
                extras.append((section, b))
                continue
            lines = _join_split_headings(b["text"].splitlines())
            top = _TOP_NUMBER.match(lines[0].split(" ", 1)[0]) if blocked and lines else None
            if top and _NUMBERED.match(lines[0]) and heading_section(lines[0]):
                last_top = int(top.group(1))  # a recognised numbered heading keeps the sequence
            standalone = standalone_top_heading(b["text"], last_top) if blocked else None
            if standalone and not heading_section(f"{standalone[0]} {standalone[1]}"):
                last_top = standalone[0]
                numbered_seen = True
                segments.append((section, "\n".join(buf)))
                buf = []
                section = parent = "methods" if _TOP_LEVEL_METHODS.search(standalone[1]) else UNCLASSIFIED
                continue
            for line in lines:
                new_section = heading_section(line)
                subsection = _is_subsection_heading(line)
                if new_section:
                    numbered = bool(_NUMBERED.match(line))
                    # Once the paper uses numbered headings, a bare word like "Methods" is usually a table header
                    # cell, not a section: drop the line as before but keep the current section label.
                    if numbered or not numbered_seen or new_section in _UNNUMBERED_OK:
                        numbered_seen = numbered_seen or numbered
                        if numbered and not subsection:
                            parent = new_section
                        segments.append((section, "\n".join(buf)))
                        buf, section = [], new_section
                elif subsection and parent and section != parent:
                    # A sub-heading naming no known section ("5.2 Performance Comparison") belongs to its parent
                    # section, e.g. back to results after "5.1 Experimental Settings" switched to methods.
                    segments.append((section, "\n".join(buf)))
                    buf, section = [], parent
                else:
                    buf.append(line)
        segments.append((section, "\n".join(buf)))
        found = []  # (section, kind, text, text_check)
        for seg_section, seg_text in segments:
            for sent in split_sentences(seg_text):
                check = None
                if blocked:
                    check = SINGLE_BLOCK if any(sent in norm for norm in body_norms) else JOINED_BLOCKS
                found.append((seg_section, BODY if blocked else None, sent, check))
        for ex_section, b in extras:
            if b["kind"] == TABLE:
                # One sentence per table/figure block: splitting cell text at "Abs." etc. would be meaningless.
                text = _clean_block(b["text"])
                pieces = [text] if len(text.split()) >= 3 else []
            else:
                pieces = split_sentences(b["text"])
            found += [(ex_section, b["kind"], t, SINGLE_BLOCK) for t in pieces]
        for idx, (sec, kind, text, check) in enumerate(found, start=1):
            row = {"id": sentence_id(paper_id, page_no, idx), "paper_id": paper_id, "page": page_no, "idx": idx,
                   "section": sec, "text": text}
            if blocked:
                row.update({"kind": kind, "text_check": check})
            out.append(row)
    if not out:
        raise NoExtractableTextError(NO_TEXT_MESSAGE)
    return out

_JUNK_META_TITLE = re.compile(
    r"^(arxiv:|microsoft word|untitled|document\d*$|paper\d*$)|\.(pdf|docx?|tex|dvi)$|^[\w-]+$", re.I)

def _usable_meta_title(title: str) -> bool:
    t = " ".join((title or "").split())
    return len(t.split()) >= 3 and not _JUNK_META_TITLE.search(t)

def _title_from_layout(page) -> str:
    """Largest-font horizontal text in the top part of page 1, joined across wrapped lines."""
    lines = []  # (y0, size, text)
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            if abs(line.get("dir", (1, 0))[1]) > 0.1:  # rotated text, e.g. arXiv's sideways stamp
                continue
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            text = " ".join(s["text"].strip() for s in spans)
            if not spans or len(re.findall(r"[A-Za-z]", text)) < 3 or text.lower().startswith("arxiv:"):
                continue
            lines.append((line["bbox"][1], max(s["size"] for s in spans), text))
    top = [ln for ln in lines if ln[0] < page.rect.height * 0.5]
    if not top:
        return ""
    biggest = max(size for _, size, _ in top)
    title_lines, started = [], False
    for _, size, text in sorted(top, key=lambda ln: ln[0]):
        if size >= biggest - 0.5:
            title_lines.append(text)
            started = True
        elif started:
            break  # title block ended (authors etc. follow in a smaller font)
    title = normalize_text(" ".join(title_lines))
    return title[:300] if len(title.split()) >= 2 else ""

def guess_title(pdf_bytes: bytes) -> str:
    """Paper title: PDF metadata if it looks like a real title, else the large-font title block on page 1."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        meta_title = (doc.metadata or {}).get("title") or ""
        if _usable_meta_title(meta_title):
            return " ".join(meta_title.split())
        if len(doc):
            from_layout = _title_from_layout(doc[0])
            if from_layout:
                return from_layout
            for line in doc[0].get_text("text").splitlines():
                if len(line.split()) >= 4 and not is_noise_line(line) and not line.strip().lower().startswith("arxiv:"):
                    return line.strip()
    return "Untitled paper"

# ---------- display-only quality flags for stored quotations ----------
_ABBREV_WORDS = {"e.g", "i.e", "etc", "al", "vs", "cf", "fig", "eq", "resp", "approx"}
_CAPTION_INSIDE = re.compile(r"\S.*?\b(Table|Figure|Fig\.)\s*\d+\s*:")
_FOOTNOTE_GLUED = re.compile(r"[a-z]\d{1,2}[A-Z][a-z]")
_FRAGMENT_AFTER_PERIOD = re.compile(r"(\S+)\.\s+([a-z]{2,6})\s")
_NUM = re.compile(r"^\d+(\.\d+)?%?$")

def layout_artifacts(text: str) -> List[str]:
    """Signs that a stored sentence was assembled across PDF layout elements. Used only to show "verify on the
    page" next to a quotation; the stored text is never changed. Codes: caption_inside, footnote_glued,
    word_fragment, table_inside_prose, cut_at_page_end."""
    flags = []
    if _CAPTION_INSIDE.search(text):
        flags.append("caption_inside")
    if _FOOTNOTE_GLUED.search(text):
        flags.append("footnote_glued")
    for m in _FRAGMENT_AFTER_PERIOD.finditer(text):
        if m.group(1).lower().rstrip(".") not in _ABBREV_WORDS and not re.search(r"\.\w$", m.group(1)):
            flags.append("word_fragment")
            break
    # Flattened table cells form RUNS of consecutive numbers ("0.5661 0.2414 0.2689 0.3552"); prose reporting
    # statistics interleaves them with words and symbols ("M = 0.41 (SD = 0.34)"), so it is not flagged.
    runs, run = [], 0
    for t in text.split():
        if t == "±" or _NUM.match(t.strip("*,;()<>")):
            run += 1
        else:
            if run:
                runs.append(run)
            run = 0
    runs.append(run)
    if sum(r >= 4 for r in runs) >= 2 or max(runs) >= 6:
        flags.append("table_inside_prose")
    if re.search(r"\w-$", text.strip()):
        flags.append("cut_at_page_end")
    return flags