"""Strict citation validation.

Every supplied ID must exist in `sentences` and belong (by its *stored* paper_id,
never by parsing the ID text) to an expected paper. A single fabricated, wrong-paper,
or blank ID makes the whole claim/judgment invalid — nothing is silently ignored.

Functions return (verified_ids, ok). `verified_ids` contains ONLY IDs that passed,
so unverified IDs can never reach the database or the frontend.
"""
from typing import Dict, Iterable, List, Optional, Set, Tuple


def clean_id(raw: Optional[str]) -> str:
    """Tolerate formatting only (brackets, quotes, whitespace) — never fuzzy-match an ID."""
    if raw is None:
        return ""
    return str(raw).strip().strip("[]()\"'` ").strip()


def _check(ids: Optional[Iterable[str]], allowed: Set[str], stored_paper: Dict[str, str]) -> Tuple[List[str], bool]:
    raw = list(ids or [])
    if not raw:
        return [], False
    verified: List[str] = []
    all_ok = True
    for item in raw:
        sid = clean_id(item)
        if not sid or stored_paper.get(sid) not in allowed:  # blank, unknown, or another paper's sentence
            all_ok = False
            continue
        if sid not in verified:
            verified.append(sid)
    return verified, all_ok


def validate_single_paper_citations(
    ids: Optional[Iterable[str]], paper_id: str, stored_paper: Dict[str, str]
) -> Tuple[List[str], bool]:
    """ok only if >= 1 ID was supplied and EVERY supplied ID exists and belongs to `paper_id`."""
    return _check(ids, {paper_id}, stored_paper)


def validate_two_paper_citations(
    ids: Optional[Iterable[str]], paper_a: str, paper_b: str, stored_paper: Dict[str, str]
) -> Tuple[List[str], bool]:
    """ok only if EVERY supplied ID exists and belongs to paper_a or paper_b, with >= 1 from EACH."""
    verified, all_ok = _check(ids, {paper_a, paper_b}, stored_paper)
    covered = {stored_paper[sid] for sid in verified}
    return verified, all_ok and covered == {paper_a, paper_b}
