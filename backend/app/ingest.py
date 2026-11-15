"""PDF -> pages -> sentences with globally unique IDs '{paper_id}:p{page}_s{n}'.

Quotes shown anywhere in ResearchLens are rebuilt from these stored sentences,
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


# Unnumbered headings still honoured after numbered ones appear (front/back matter of e.g. ACL papers).
_UNNUMBERED_OK = {"abstract", "limitations", "other"}


def heading_section(line: str):
    """Canonical section name if `line` is a section heading, else None."""
    if not (_NOISE_LINE[0].match(line) or _NOISE_LINE[1].match(line)):
        return None
    for pattern, name in _SECTION_CANON:
        if pattern.search(line):
            return name
    return None  # a sub-heading like "3.1 Study design": keep the current section


def build_sentences(paper_id: str, pages: List[str]) -> List[Dict]:
    """Sentences with IDs, page numbers and the section they appear in ('front' before any heading)."""
    check_paper_id(paper_id)
    out: List[Dict] = []
    section = "front"
    parent = None  # section of the last top-level numbered heading ("5 Experiments" -> results)
    numbered_seen = False
    for page_no, page_text in enumerate(pages, start=1):
        idx = 0
        segments, buf = [], []
        for line in _join_split_headings(page_text.splitlines()):
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
        for seg_section, seg_text in segments:
            for sent in split_sentences(seg_text):
                idx += 1
                out.append({
                    "id": sentence_id(paper_id, page_no, idx),
                    "paper_id": paper_id,
                    "page": page_no,
                    "idx": idx,
                    "section": seg_section,
                    "text": sent,
                })
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
