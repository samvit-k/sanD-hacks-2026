"""Author lists and shared authorship, derived on read from each paper's stored first-page sentence.

Nothing here is stored: author lists are recomputed from the verbatim front-matter sentence (cited by its
sentence ID) whenever a graph, impact summary or chat context is built, and cached only for that request.

Deliberately conservative:
- A list is "parsed" only when every author slot is unambiguous (a name followed by an affiliation marker,
  a comma, "and", or a clear end of the author block). Anything incomplete or ambiguous is "unknown".
- A shared author needs the same first given name AND surname (middle names/initials may be absent on one side
  but must not conflict). An initial-only given name ("C. Wu") can only produce a "possible" match.
- "No shared authors detected" is NOT independence: same lab, dataset, funding or data are never checked here.
  Independence is therefore never established by this module.
"""
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

PARSED, UNKNOWN = "parsed", "unknown"
# Pair statuses
SHARED, POSSIBLE, NONE_DETECTED, PAIR_UNKNOWN = ("shared_authors", "possible_shared_authors",
                                                 "no_shared_authors_detected", "unknown")
MIN_AUTHORS, MAX_AUTHORS = 1, 40

_MARKERS = r"\d*⋆∗†‡§¶♯♮#"
_MARKER_RUN = re.compile(rf"^[{_MARKERS}\s,]+")
_CUT = re.compile(r"\{|@|\bAbstract\b|https?://|\bKeywords?\b", re.I)
# One name word: a capitalised word (letters, apostrophes, hyphens), or initials like "R." / "C.K.".
_WORD = r"(?:[A-ZÀ-Ý][a-zà-ÿ'’]+(?:-[A-ZÀ-Ýa-zà-ÿ][a-zà-ÿ'’]+)*|[A-Z]\.(?:\s?[A-Z]\.)*)"
_NAME = re.compile(rf"^({_WORD}(?:\s+{_WORD}){{1,3}})")
_AFFILIATION_WORDS = {
    "university", "institute", "institution", "college", "school", "department", "dept", "faculty", "laboratory",
    "lab", "labs", "research", "technologies", "technology", "inc", "corporation", "corp", "center", "centre",
    "academy", "hospital", "foundation", "group", "microsoft", "google", "amazon", "meta", "ibm", "usa", "china",
}


def _plain(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).split())


def _fold(s: str) -> str:
    """Lowercase, accents removed, hyphens as spaces (for matching only; display keeps the source spelling)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().replace("-", " ").replace("’", "'").split())


def _after_title(text: str, title: str) -> Optional[str]:
    t, ttl = _plain(text), _plain(title)
    i = t.lower().find(ttl.lower())
    return t[i + len(ttl):] if ttl and i >= 0 else None


def parse_author_block(text: str, title: str) -> Dict:
    """Authors listed after the title in a front-matter sentence. Returns {status, names, reason}."""
    rest = _after_title(text, title)
    if rest is None:
        return {"status": UNKNOWN, "names": [], "reason": "title not found in the front-matter sentence"}
    m = _CUT.search(rest)
    block = rest[: m.start()] if m else rest
    chunks = [c.strip() for c in re.split(r",|\band\b|;", block)]
    names: List[str] = []
    ended = False
    for chunk in chunks:
        if not chunk:
            continue
        if _MARKER_RUN.fullmatch(chunk):          # "2" in "Elkins1,2": a marker-only chunk
            continue
        if re.match(r"^[\d⋆∗†‡§¶♯]", chunk):      # "1 McGill University", "†Microsoft": affiliations begin
            ended = True
            break
        nm = _NAME.match(chunk)
        if not nm:
            return {"status": UNKNOWN, "names": names, "reason": f"could not read an author name in “{chunk[:40]}”"}
        name = nm.group(1)
        words = name.replace(".", " ").split()
        if any(w.lower() in _AFFILIATION_WORDS for w in words):
            return {"status": UNKNOWN, "names": names, "reason": f"author and affiliation run together: “{chunk[:40]}”"}
        tail = chunk[nm.end():]
        markers = _MARKER_RUN.match(tail)
        after = tail[markers.end():] if markers else tail
        if after.strip():
            # Something follows the name inside this slot. Only acceptable for the LAST author when a marker
            # separates it from the affiliation ("Hamed Zamani1 1University ..."); otherwise ambiguous.
            if not (markers and markers.group(0).strip()):
                return {"status": UNKNOWN, "names": names,
                        "reason": f"author and affiliation run together: “{chunk[:40]}”"}
            names.append(name)
            ended = True
            break
        names.append(name)
    if not names:
        return {"status": UNKNOWN, "names": [], "reason": "no author names found after the title"}
    if not ended and not m:
        return {"status": UNKNOWN, "names": names, "reason": "the author block has no clear end"}
    if len(names) > MAX_AUTHORS:
        return {"status": UNKNOWN, "names": names, "reason": "implausibly many author names"}
    return {"status": PARSED, "names": names, "reason": None}


def paper_authors(paper: Dict, page1_sentences: Iterable[Dict]) -> Dict:
    """Author list for one paper from its stored page-1 sentences (first one containing the title)."""
    for s in page1_sentences:
        if _after_title(s["text"], paper["title"]) is not None:
            out = parse_author_block(s["text"], paper["title"])
            return {**out, "source_sentence_id": s["id"], "method": "parsed from the stored front-matter sentence"}
    return {"status": UNKNOWN, "names": [], "reason": "no first-page sentence contains the paper title",
            "source_sentence_id": None, "method": "parsed from the stored front-matter sentence"}


def _key(name: str) -> Tuple[str, List[str], str]:
    parts = _fold(name).replace(".", ". ").split()
    return parts[0], parts[1:-1], parts[-1]


def _initial(w: str) -> bool:
    return len(w.rstrip(".")) == 1


def match_names(a: str, b: str) -> Optional[str]:
    """'same' | 'possible' | None. Same surname required; given names must agree conservatively."""
    fa, ma, la = _key(a)
    fb, mb, lb = _key(b)
    if la != lb:
        return None
    if _initial(fa) or _initial(fb):
        return "possible" if fa[0] == fb[0] else None
    if fa != fb:
        return None
    # Middle names: may be missing on one side, but present ones must not conflict (by initial).
    if ma and mb and [w[0] for w in ma] != [w[0] for w in mb]:
        return None
    return "same"


def shared_authors(a: Dict, b: Dict) -> Dict:
    """Pair assessment between two papers' author lists. Never asserts independence."""
    basis = [s for s in (a.get("source_sentence_id"), b.get("source_sentence_id")) if s]
    same, possible = [], []
    for x in a.get("names", []):
        for y in b.get("names", []):
            kind = match_names(x, y)
            if kind == "same":
                same.append(x if x == y else f"{x} / {y}")
            elif kind == "possible":
                possible.append(f"{x} / {y}")
    if same:
        status = SHARED
    elif possible:
        status = POSSIBLE
    elif a.get("status") == PARSED and b.get("status") == PARSED:
        status = NONE_DETECTED
    else:
        status = PAIR_UNKNOWN
    return {"status": status, "shared": same, "possible": possible, "basis_sentence_ids": basis,
            "independence_established": False, "review_needed": status != NONE_DETECTED,
            "summary": independence_summary(status, same, possible, a, b)}


def independence_summary(status: str, same: List[str], possible: List[str], a: Dict, b: Dict) -> str:
    if status == SHARED:
        n = len(same)
        return (f"Independence not established — these papers share {n} author{'s' if n != 1 else ''} "
                f"({', '.join(same)}). Shared authorship links the studies; it does not by itself mean the same data "
                "or experiments.")
    if status == POSSIBLE:
        return (f"Independence not established — possible shared authors ({', '.join(possible)}); the author lists "
                "use initials, so this could not be confirmed.")
    if status == PAIR_UNKNOWN:
        why = [x.get("reason") for x in (a, b) if x.get("status") != PARSED and x.get("reason")]
        return ("Independence not established — the author list of at least one paper could not be read reliably"
                + (f" ({why[0]})" if why else "") + ".")
    return ("No shared authors detected in the papers' author lists. This does not establish independence: shared "
            "data, labs or funding are not checked.")


# Labels whose user-facing meaning leans on the evidence being separate: "supports" reads as corroboration and
# "strengthens" was defined to the model as "adds independent support".
SUPPORTIVE = {"supports", "strengthens"}
REVIEW_HEADLINE = "Independence not established — review needed"


def independence_flag(label: Optional[str], pair: Dict, insufficient: bool = False) -> Dict:
    """How prominently to qualify a relationship/impact label. level: 'review' (prominent), 'note' (shown with the
    label: supporting evidence, independence not verified) or None (dependence shown as context only)."""
    if insufficient or label not in SUPPORTIVE:
        return {"level": None, "headline": None}
    if pair["status"] == NONE_DETECTED:
        return {"level": "note", "headline": "Supporting evidence — independence not verified"}
    return {"level": "review", "headline": REVIEW_HEADLINE}


class AuthorIndex:
    """Per-request cache: author lists per paper and pair assessments."""

    def __init__(self, papers: Iterable[Dict], page1: Dict[str, List[Dict]]):
        self.lists = {p["id"]: paper_authors(p, page1.get(p["id"], [])) for p in papers}
        self._pairs: Dict[Tuple[str, str], Dict] = {}

    def pair(self, pa: str, pb: str) -> Dict:
        k = (pa, pb) if pa <= pb else (pb, pa)
        if k not in self._pairs:
            empty = {"status": UNKNOWN, "names": [], "reason": "paper not loaded", "source_sentence_id": None}
            self._pairs[k] = shared_authors(self.lists.get(k[0], empty), self.lists.get(k[1], empty))
        return self._pairs[k]
