"""Evidence-grounded research assistant ("GraphRAG-lite").

For each question we retrieve evidence from PostgreSQL/pgvector:
  1. the graph item the user attached (relationship, claim, paper or the central question),
  2. claims the user names as "Node N" (stable numbers from `node_refs`),
  3. the top-k claims nearest to the question embedding,
then add their evidence relationships, the latest new-paper impacts (when relevant), and the
verbatim source sentences behind all of it. Gemini answers ONLY from that context.

Integrity rules (same spirit as extraction/comparison) — any failure withholds the whole answer:
  - every cited sentence ID must be one of the sentences shown to the model (no silent dropping);
  - graph references must use tokens shown to the model ([[claim:3]], [[rel:5]], ...);
  - citations are required by default: an answer with no verified citation passes only if every
    clause is, IN ITS ENTIRETY, a non-assertive template (greeting, offer to help with a graph
    item, clarification request, or a statement that evidence is missing) or a question. Any other
    clause counts as an unsupported research statement, whatever the insufficient_evidence flag says;
  - text presented as a direct quotation (a quoted span of 4+ words) must appear verbatim in a
    shown source sentence; the matching sentence is then cited.
Quotes displayed as verified evidence are never taken from model text: they are rebuilt from
`sentences` when messages are read.

Every read/write is scoped by `session_id` so Stage D sessions need no chat rewrite.
"""
import logging
import re
import unicodedata
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import db
from .citations import clean_id
from .contracts import ChatAnswer
from .authors import AuthorIndex, POSSIBLE, REVIEW_HEADLINE, SHARED
from .graph import paper_detail, quote, ref_order
from .llm import LLM

log = logging.getLogger(__name__)

DEFAULT_SESSION = "default"
MAX_MESSAGE_LEN = 2000
TOP_K = 6                 # nearest claims by embedding
MAX_CLAIMS = 14           # cap on claims placed in the prompt
HISTORY_TURNS = 6         # earlier messages included for conversational continuity
MIN_QUOTE_WORDS = 4       # quoted spans this long are treated as claimed paper text

SESSION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
TOKEN_RE = re.compile(r"\[\[(q|paper:[a-z0-9][a-z0-9-]*|claim:\d+|rel:\d+)\]\]")
ANY_TOKEN_RE = re.compile(r"\[\[[^\]]*\]\]")
NODE_MENTION_RE = re.compile(r"\bnodes?\b((?:\s*(?:#?\d+|,|and|&|or))+)", re.I)
IMPACT_HINT_RE = re.compile(r"\b(new|latest|recent|upload\w*|evaluat\w*|chang\w*|impact\w*)\b", re.I)
QUOTE_RE = re.compile(r"[\"“”]([^\"“”]+)[\"“”]")
# Sentence IDs written into the answer prose (e.g. "... gains [paper-x:p6_s6]."). They are validated like
# cited_sentence_ids and then removed from the text: verified sources are listed separately in the UI.
_SID = r"[a-z0-9][a-z0-9-]*:p\d+_s\d+"
INLINE_SID_RE = re.compile(_SID)
INLINE_CITE_RE = re.compile(rf"\s*(?:[\[(]\s*{_SID}(?:\s*[,;]\s*{_SID})*\s*[\])]|{_SID})")

# --- citation-free answers -------------------------------------------------------------------
# Default: every clause needs a citation. A clause is exempt only if the WHOLE clause (after
# normalization) matches one of these templates, so "I can confirm that X improves Y" or
# "Please note that X increased Y" are never exempt just because they start politely.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r"[;:,]|\s[-–—]\s|\b(?:but|however|although|though|whereas|while|yet|because|since|"
                           r"so|therefore|thus|hence)\b", re.I)
# A graph item or a generic part of the workspace (tokens are normalized to "item" first).
_ITEM = (r"(?:item|node \d+|nodes \d+(?: (?:and|or) \d+)?|"
         r"(?:this|that|the|your|a|an) (?:item|node|claim|paper|connection|relationship|edge|question)s?|"
         r"(?:the|your) (?:graph|library|papers|claims|connections|relationships|evidence|sources|central question|"
         r"research question|question)|it|them|that|this)")
# Optional UI location after an item ("select a connection in the graph") — places, never claims.
_WHERE = (r"(?: (?:in|on|from) the (?:graph|canvas|inspector|library|paper list|left panel|right panel|sidebar|"
          r"workspace))?")
_ITEMS = rf"{_ITEM}(?: (?:and|or) {_ITEM})*{_WHERE}"
_EVIDENCE = r"(?:verified |direct |relevant |enough |sufficient |available )?(?:evidence|information|data|sources?)"
_SCOPE = r"(?: (?:in|from|within|among) (?:the|your|these|this) (?:library|graph|papers|sources|evidence|claims))?"
# A graph item, or a short topic phrase without digits (numbers would be a finding, e.g. "about the 25% gain").
# Topic words may not be conjunctions/relative pronouns, so a second statement cannot hide inside the topic.
_TOPIC_WORD = r"(?!(?:and|or|but|that|which|who|because|so|while|whereas|yet)\b)[a-z][a-z'-]*"
_TOPIC = (rf"(?: (?:about|on|for|regarding|concerning) (?:{_ITEMS}|{_TOPIC_WORD}(?: {_TOPIC_WORD}){{0,7}}))?")
_ANSWER_THAT = r"(?: to (?:answer|address|assess|judge) (?:that|this|the|your) question)?"
_NON_ASSERTIVE_TEMPLATES = [re.compile(p) for p in (
    # greetings / acknowledgements
    r"(?:hello|hi|hey)(?: there)?|thanks|thank you|you're welcome|sure|okay|of course|happy to help",
    # offers to help with a graph item
    rf"(?:i|we) (?:can|could|would be happy to|am happy to|'d be happy to) help(?: you)?"
    rf"(?: (?:inspect|explore|review|examine|look at|navigate|compare|understand|find) {_ITEMS})?",
    rf"(?:i|we) (?:can|could) (?:show|explain|walk you through|summarize) {_ITEMS}",
    rf"you (?:can|could|may|might) (?:select|click|open|ask about|upload|inspect) {_ITEMS}",
    # clarification requests
    r"(?:please )?(?:clarify|specify|tell me)(?: which (?:paper|node|claim|connection|item|relationship)"
    r"(?: you mean| you are asking about)?| your question| what you mean)?",
    r"(?:please )?(?:select|choose) (?:a|one|the) (?:node|claim|paper|connection|relationship)(?: in the graph)?",
    r"(?:please )?rephrase(?: your question| the question)?",
    # explicit statements that evidence is missing
    rf"there (?:is|are|was) (?:insufficient|not enough|no) {_EVIDENCE}{_SCOPE}{_ANSWER_THAT}{_TOPIC}{_SCOPE}",
    rf"(?:the )?(?:available )?evidence{_SCOPE} (?:is|was) (?:insufficient|unavailable|missing|not available)"
    rf"{_ANSWER_THAT}{_TOPIC}",
    rf"{_ITEMS}(?: do| does)? (?:not|n't) (?:contain|include|address|cover|report|provide) (?:any |enough )?"
    rf"{_EVIDENCE}{_TOPIC}{_ANSWER_THAT}",
    rf"(?:i|we) (?:cannot|can't|could not|couldn't|am unable to|was unable to) (?:answer|determine|assess|judge|find)"
    rf" (?:that|this|it|the question|your question|an answer|{_EVIDENCE}{_TOPIC})"
    rf"(?: (?:from|with|using|in) (?:the|your) (?:available |stored )?(?:evidence|papers|library|graph|sources))?",
)]

def _clause_form(clause: str) -> str:
    c = unicodedata.normalize("NFKC", clause).lower().replace("’", "'")
    c = re.sub(r"\s+", " ", c)
    return c.strip(" .!\"'“”()")

def is_non_assertive(clause: str) -> bool:
    form = _clause_form(clause)
    return any(t.fullmatch(form) for t in _NON_ASSERTIVE_TEMPLATES)

GUARDRAIL_CITATIONS = "invalid_citations"
GUARDRAIL_REFERENCES = "invalid_references"
GUARDRAIL_UNCITED = "uncited_findings"
GUARDRAIL_QUOTE = "unverified_quotation"
GUARDRAIL_INDEPENDENCE = "independence_overstated"
WITHHELD = {
    GUARDRAIL_CITATIONS: "The assistant’s answer cited sources that could not be verified against the stored papers, "
                         "so it is not shown.",
    GUARDRAIL_REFERENCES: "The assistant’s answer referred to graph items that do not exist, so it is not shown.",
    GUARDRAIL_UNCITED: "The assistant’s answer made research statements without citing any verified source sentence, "
                       "so it is not shown as a supported answer.",
    GUARDRAIL_QUOTE: "The assistant’s answer contained a quotation that does not match the stored paper text, "
                     "so it is not shown.",
    GUARDRAIL_INDEPENDENCE: "The assistant’s answer presented related findings as confirmation without acknowledging "
                            "that the papers share authors (or claimed independent confirmation, which SpiderMonkey "
                            "cannot establish), so it is not shown.",
}
WITHHELD_HINT = " Try rephrasing, or ask about a specific node or connection."
CONTEXT_KINDS = ("question", "paper", "claim", "relationship")

class ChatUnavailableError(RuntimeError):
    """Gemini failed while answering; nothing was stored."""

def check_session(session_id: Optional[str]) -> str:
    sid = (session_id or DEFAULT_SESSION).strip()
    if not SESSION_RE.match(sid):
        raise ValueError("session_id must match [a-z0-9][a-z0-9-]{0,39}")
    return sid

def check_message(message: str) -> str:
    text = (message or "").strip()
    if not text:
        raise ValueError("The message must not be empty.")
    if len(text) > MAX_MESSAGE_LEN:
        raise ValueError(f"The message must be at most {MAX_MESSAGE_LEN} characters.")
    return text

def check_context(context: Optional[Dict]) -> Optional[Dict]:
    if not context:
        return None
    kind, cid = context.get("kind"), context.get("id")
    if kind not in CONTEXT_KINDS:
        raise ValueError(f"context.kind must be one of {list(CONTEXT_KINDS)}")
    if kind in ("claim", "relationship"):
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            raise ValueError("context.id must be an integer for claims and relationships")
    elif kind == "paper":
        cid = str(cid or "")
    else:
        cid = "q"
    return {"kind": kind, "id": cid}

def ensure_context_exists(conn, context: Optional[Dict]) -> None:
    """The attached graph item must exist in THIS session (the connection is session-scoped). An id from another
    session — or a deleted item — is rejected instead of being silently ignored."""
    if not context or context["kind"] == "question":
        return
    table = {"claim": "claims", "relationship": "relationships", "paper": "papers"}[context["kind"]]
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = %s", (context["id"],)).fetchone():
        raise ValueError(f"The attached {context['kind']} does not exist in this research session.")

def mentioned_numbers(text: str) -> List[int]:
    """'Why do Nodes 4 and 7 disagree?' -> [4, 7]"""
    out: List[int] = []
    for m in NODE_MENTION_RE.finditer(text):
        for n in re.findall(r"\d+", m.group(1)):
            if int(n) not in out:
                out.append(int(n))
    return out

def _unique(xs: Iterable) -> List:
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out

# ---------- retrieval ----------
def gather_context(conn, llm: LLM, message: str, context: Optional[Dict]) -> Dict:
    papers = db.all_papers(conn)
    claims_all = db.all_claims(conn)
    refs = db.assign_node_refs(conn, ref_order(papers, claims_all))
    by_num = {n: node for node, n in refs.items()}
    claim_rows = {c["id"]: c for c in claims_all}
    rels_all = {r["id"]: r for r in db.all_relationships(conn)}

    focus: List[int] = []         # claims the user pointed at (context item or "Node N")
    focus_rels: List[int] = []
    focus_papers: List[str] = []
    if context:
        if context["kind"] == "relationship" and context["id"] in rels_all:
            r = rels_all[context["id"]]
            focus_rels.append(r["id"])
            focus += [r["claim_a"], r["claim_b"]]
        elif context["kind"] == "claim" and context["id"] in claim_rows:
            focus.append(context["id"])
        elif context["kind"] == "paper":
            focus_papers.append(context["id"])
            focus += [c["id"] for c in claims_all if c["paper_id"] == context["id"]]
    for n in mentioned_numbers(message):
        node = by_num.get(n)
        if node and node.startswith("claim:"):
            focus.append(int(node.split(":", 1)[1]))
        elif node and node.startswith("paper:"):
            pid = node.split(":", 1)[1]
            focus_papers.append(pid)
            focus += [c["id"] for c in claims_all if c["paper_id"] == pid]

    nearest: List[int] = []
    if claims_all:
        nearest = db.nearest_claims(conn, llm.embed([message])[0], TOP_K)

    selected = _unique([c for c in focus if c in claim_rows] + nearest)[:MAX_CLAIMS]
    # Relationships: everything touching a focus claim (pulling in the other side), plus any
    # relationship between two retrieved claims.
    focus_set = set(focus)
    rels: List[Dict] = []
    for r in rels_all.values():
        touches_focus = r["claim_a"] in focus_set or r["claim_b"] in focus_set
        inside = r["claim_a"] in selected and r["claim_b"] in selected
        if r["id"] in focus_rels or touches_focus or inside:
            rels.append(r)
            for cid in (r["claim_a"], r["claim_b"]):
                if cid not in selected and len(selected) < MAX_CLAIMS:
                    selected.append(cid)
    rels = [r for r in rels if r["claim_a"] in selected and r["claim_b"] in selected]

    impacts: List[Dict] = []
    latest = db.latest_new_paper(conn)
    if latest:
        new_claims = {c["id"] for c in claims_all if c["paper_id"] == latest["id"]}
        if IMPACT_HINT_RE.search(message) or new_claims & set(selected):
            impacts = db.impacts_for_paper(conn, latest["id"])
            for i in impacts:
                for cid in (i["new_claim_id"], i["prior_claim_id"]):
                    if cid not in selected and len(selected) < MAX_CLAIMS + 4:
                        selected.append(cid)
            impacts = [i for i in impacts if i["new_claim_id"] in selected and i["prior_claim_id"] in selected]

    claims = [claim_rows[c] for c in selected if c in claim_rows]
    sentence_ids = [s for c in claims for s in c["sentence_ids"]]
    sentence_ids += [s for r in rels if not r.get("insufficient_evidence") for s in (r.get("cited_sentence_ids") or [])]
    sentence_ids += [s for i in impacts if not i.get("insufficient_evidence") for s in (i.get("cited_sentence_ids") or [])]
    sentences = db.sentences_by_ids(conn, sentence_ids)
    authors = AuthorIndex(papers, db.page1_sentences(conn, [p["id"] for p in papers]))
    return {
        "authors": authors,
        "papers": papers, "claims": claims, "relationships": rels, "impacts": impacts,
        "sentences": sentences, "refs": refs, "latest": latest,
        "focus_rels": focus_rels, "focus_claims": [c for c in focus if c in claim_rows], "focus_papers": focus_papers,
    }

# ---------- prompt ----------
RULES = """You are the SpiderMonkey research assistant. Answer the user's question using ONLY the evidence below.
Rules:
- Ground every research statement in the SOURCE SENTENCES and put their full IDs in cited_sentence_ids. Never cite an ID that is not listed below. An answer that makes research statements without citations will be rejected.
- Refer to papers, claims and relationships ONLY with the tokens shown below, written exactly, e.g. [[claim:3]], [[paper:x-1a2b3c]], [[rel:5]], [[q]]. Never invent tokens or node numbers. Users call items "Node N"; the mapping is shown next to each token.
- Do not write sentence IDs in the answer text; list them only in cited_sentence_ids.
- Paraphrase in your own words. Do not put paper text in quotation marks unless you copy it EXACTLY from a source sentence below; the app shows verified quotations separately.
- Claims are model-extracted paraphrases; source sentences are verbatim. Earlier relationship labels and impacts are model assessments, not established facts — say so when you rely on them.
- If the evidence below cannot answer the question, set insufficient_evidence to true and only say what is missing (e.g. "There is insufficient evidence in the library about long-term retention."); do not state findings in that case. Never invent findings, numbers, quotes, studies or citations.
- Never call a finding true, false, proven, disproved or debunked; describe how the evidence relates. A non-significant result does not refute a significant one. Note when a paper is abstract-only.
- Independence: never describe evidence as independent, or as independent confirmation, corroboration or replication — this library cannot establish independence. When two papers SHARE AUTHORS (listed below), any statement that one supports, confirms or strengthens the other must say that they share authors, so it is supporting evidence from related work, not independent corroboration. Shared authorship does not by itself mean the same data or experiments.
- Relate the answer to the central research question when relevant. Be concise (at most 180 words), plain prose, no headings."""

def _pair_note(ctx: Dict, claim_a: int, claim_b: int) -> Optional[str]:
    if not ctx.get("authors"):
        return None
    cp = {c["id"]: c["paper_id"] for c in ctx["claims"]}
    if claim_a not in cp or claim_b not in cp:
        return None
    return ctx["authors"].pair(cp[claim_a], cp[claim_b])["summary"]

def build_chat_prompt(question: str, message: str, ctx: Dict, history: List[Dict]) -> Tuple[str, Dict[str, str], Set[str]]:
    """Returns (prompt, {shown sentence id: text}, allowed tokens)."""
    refs = ctx["refs"]
    tokens: Set[str] = {"q"}
    shown: Dict[str, str] = {}

    def show(sid: str, indent: str = "  ") -> Optional[str]:
        s = ctx["sentences"].get(sid)
        if not s or sid in shown:
            return None
        shown[sid] = s["text"]
        return f"{indent}[{sid}] {s['text']}"

    lines = [RULES, "", f"CENTRAL RESEARCH QUESTION [[q]]: {question}", "", "PAPERS:"]
    for p in ctx["papers"]:
        tok = f"paper:{p['id']}"
        tokens.add(tok)
        notes = ["abstract only" if p.get("access_level") == "abstract" else "full text"]
        if p.get("is_new"):
            notes.append("most recently evaluated new paper" if ctx["latest"] and ctx["latest"]["id"] == p["id"]
                         else "evaluated paper")
        lines.append(f"[[{tok}]] (Node {refs.get(tok, '?')}) \"{p['title']}\" — {', '.join(notes)}")
        al = ctx["authors"].lists.get(p["id"]) if ctx.get("authors") else None
        if al:
            lines.append(f"  authors: {', '.join(al['names'])}" if al["status"] == "parsed"
                         else "  authors: could not be read reliably")
    lines += ["", "CLAIMS (model-extracted paraphrases) with their verbatim SOURCE SENTENCES:"]
    for c in ctx["claims"]:
        tok = f"claim:{c['id']}"
        tokens.add(tok)
        conds = {k: v for k, v in (c.get("conditions") or {}).items() if v}
        lines.append(f"[[{tok}]] (Node {refs.get(tok, '?')}) from [[paper:{c['paper_id']}]]: {c['text']}")
        if conds:
            lines.append(f"  conditions: {conds}")
        lines += [ln for ln in (show(sid) for sid in c["sentence_ids"]) if ln]
    if ctx["relationships"]:
        lines += ["", "EVIDENCE RELATIONSHIPS (earlier model assessments):"]
        for r in ctx["relationships"]:
            tok = f"rel:{r['id']}"
            tokens.add(tok)
            head = f"[[{tok}]] [[claim:{r['claim_a']}]] <-> [[claim:{r['claim_b']}]]"
            if r.get("insufficient_evidence"):
                lines.append(f"{head}: no judgment available (insufficient verified evidence)")
                continue
            diffs = "; ".join(r.get("key_differences") or [])
            lines.append(f"{head}: {r['label']} — {r.get('explanation', '')}" + (f" Key differences: {diffs}" if diffs else ""))
            dep = _pair_note(ctx, r["claim_a"], r["claim_b"])
            if dep:
                lines.append(f"  independence: {dep}")
            lines += [ln for ln in (show(sid) for sid in r.get("cited_sentence_ids") or []) if ln]
    if ctx["impacts"]:
        latest = ctx["latest"]
        lines += ["", f"IMPACT OF THE LATEST EVALUATED PAPER [[paper:{latest['id']}]] (model assessments):"]
        for i in ctx["impacts"]:
            if i.get("insufficient_evidence"):
                verdict = "no judgment (insufficient verified evidence)"
            else:
                verdict = f"{i['impact']} — {i.get('rationale', '')}"
            lines.append(f"new [[claim:{i['new_claim_id']}]] -> earlier [[claim:{i['prior_claim_id']}]]: {verdict}")
            dep = _pair_note(ctx, i["new_claim_id"], i["prior_claim_id"])
            if dep:
                lines.append(f"  independence: {dep}")
            if not i.get("insufficient_evidence"):
                lines += [ln for ln in (show(sid) for sid in i.get("cited_sentence_ids") or []) if ln]
    focus = [f"[[rel:{r}]]" for r in ctx["focus_rels"]] + [f"[[claim:{c}]]" for c in ctx["focus_claims"]] + \
            [f"[[paper:{p}]]" for p in ctx["focus_papers"] if f"paper:{p}" in tokens]
    if focus:
        lines += ["", f"ITEMS THE USER SELECTED OR NAMED: {', '.join(_unique(focus)[:8])}"]
    if history:
        lines += ["", "RECENT CONVERSATION:"]
        for m in history[-HISTORY_TURNS:]:
            lines.append(f"{m['role']}: {m['content'][:600]}")
    lines += ["", f"USER QUESTION: {message}"]
    return "\n".join(lines), shown, tokens

# ---------- validation ----------
def _norm(text: str) -> str:
    """Comparison form for quotations: Unicode-normalized, case- and whitespace-insensitive,
    curly quotes/apostrophes and dashes unified, edge punctuation dropped."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .,;:!?'\"()[]…")

def quoted_spans(answer: str) -> List[str]:
    """Spans presented as direct quotations: double-quoted text of MIN_QUOTE_WORDS+ words.
    Shorter quoted terms ("Node 3", "apparent contradiction") are ordinary language."""
    without_tokens = ANY_TOKEN_RE.sub(" ", answer or "")
    return [m.strip() for m in QUOTE_RE.findall(without_tokens) if len(m.split()) >= MIN_QUOTE_WORDS]

def match_quotation(span: str, shown: Dict[str, str]) -> Optional[str]:
    """ID of the shown source sentence containing `span` verbatim (modulo case/whitespace), else None."""
    needle = _norm(span)
    if not needle:
        return None
    for sid, text in shown.items():
        if needle in _norm(text):
            return sid
    return None

def assertive_clauses(answer: str) -> List[str]:
    """Clauses that must be backed by a citation. A sentence that is a question is exempt; otherwise
    each clause is exempt only if it matches a non-assertive template IN FULL."""
    text = ANY_TOKEN_RE.sub(" item ", answer or "")
    out: List[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            if re.search(r"[A-Za-z]{2,}", clause) and not is_non_assertive(clause):
                out.append(clause.strip())
    return out

def strip_inline_citations(text: str) -> str:
    """Remove sentence IDs from prose (and the space before them), keeping punctuation tidy."""
    out = INLINE_CITE_RE.sub("", text or "")
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()

# --- independence language ---------------------------------------------------------------------------------
# Words that present one finding as backing another. Between papers that share authors they must come with an
# acknowledgement of the shared authorship; "independent(ly)" is never asserted (it cannot be established here).
CONFIRM_RE = re.compile(r"\b(confirm\w*|corroborat\w*|replicat\w*|reproduc\w*|validat\w*|strengthen\w*|support\w*|"
                        r"agree\w*|consistent with|in line with|echo\w*)\b", re.I)
INDEPENDENT_RE = re.compile(r"\bindependent(?:ly)?\b", re.I)
NEGATED_BEFORE_RE = re.compile(r"\b(not|no|never|cannot|can't|isn't|aren't|wasn't|without|lacks?|unclear|unknown|"
                               r"uncertain|unverified|not established|rather than|instead of|whether)\b[^.;!?]{0,60}$",
                               re.I)
NEGATED_AFTER_RE = re.compile(r"^[^.;!?]{0,25}\b(not established|unverified|unknown|cannot be established|is unclear)\b",
                              re.I)
AUTHOR_MENTION_RE = re.compile(r"\b(co-?)?authors?(hip)?\b|\bsame (?:research )?(?:group|team|lab)\b", re.I)

def referenced_papers(content: str, claim_paper: Dict[int, str], rel_claims: Dict[int, Tuple[int, int]]) -> Set[str]:
    out: Set[str] = set()
    for tok in TOKEN_RE.findall(content or ""):
        kind, _, ident = tok.partition(":")
        if kind == "paper":
            out.add(ident)
        elif kind == "claim" and int(ident) in claim_paper:
            out.add(claim_paper[int(ident)])
        elif kind == "rel" and int(ident) in rel_claims:
            out.update(claim_paper[c] for c in rel_claims[int(ident)] if c in claim_paper)
    return out

def independence_issues(content: str, papers: Set[str], authors: Optional[AuthorIndex]) -> List[Dict]:
    """Deterministic check of an answer's independence language. Returns one issue per problem found."""
    text = ANY_TOKEN_RE.sub(" item ", content or "")
    issues: List[Dict] = []
    for m in INDEPENDENT_RE.finditer(text):
        if not (NEGATED_BEFORE_RE.search(text[:m.start()]) or NEGATED_AFTER_RE.search(text[m.end():])):
            issues.append({"kind": "independence_claimed",
                           "detail": "The answer describes evidence as independent; SpiderMonkey cannot establish "
                                     "independence between papers."})
            break
    if authors is None or not CONFIRM_RE.search(text) or AUTHOR_MENTION_RE.search(text):
        return issues
    ids = sorted(papers)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pair = authors.pair(a, b)
            if pair["status"] in (SHARED, POSSIBLE):
                issues.append({"kind": "shared_authors_unacknowledged", "papers": [a, b],
                               "detail": pair["summary"]})
    return issues

def _withheld(reason: str) -> Dict:
    return {"content": WITHHELD[reason] + WITHHELD_HINT, "cited_sentence_ids": [], "insufficient_evidence": True,
            "guardrail": reason}

def validate_answer(ans: ChatAnswer, shown: Dict[str, str], tokens: Set[str],
                    independence: Optional[Callable[[str], List[Dict]]] = None) -> Dict:
    """Strict: any failed check withholds the whole answer; nothing is silently repaired.
    `independence(answer) -> issues` checks independence language (see independence_issues)."""
    answer = (ans.answer or "").strip()
    # 1. Citation IDs: every one must be a sentence that was shown to the model.
    verified: List[str] = []
    for raw in ans.cited_sentence_ids or []:
        sid = clean_id(raw)
        if not sid or sid not in shown:
            return _withheld(GUARDRAIL_CITATIONS)
        if sid not in verified:
            verified.append(sid)
    # 1b. Sentence IDs written in the prose are citations too: validate them, then strip them from the text.
    for sid in INLINE_SID_RE.findall(answer):
        if sid not in shown:
            return _withheld(GUARDRAIL_CITATIONS)
        if sid not in verified:
            verified.append(sid)
    answer = strip_inline_citations(answer)
    # 2. Graph references: only tokens that were shown, and no malformed look-alikes.
    found_tokens = TOKEN_RE.findall(answer)
    if any(t not in tokens for t in found_tokens) or len(ANY_TOKEN_RE.findall(answer)) != len(found_tokens):
        return _withheld(GUARDRAIL_REFERENCES)
    # 3. Direct quotations must be verbatim source text; the matched sentence is the one quoted, so cite it.
    for span in quoted_spans(answer):
        sid = match_quotation(span, shown)
        if sid is None:
            return _withheld(GUARDRAIL_QUOTE)
        if sid not in verified:
            verified.append(sid)
    # 4. Citations are required by default; the insufficient_evidence flag never exempts a statement.
    if not verified and assertive_clauses(answer):
        return _withheld(GUARDRAIL_UNCITED)
    # 5. Never present dependent studies as confirmation without saying so, and never assert independence.
    if independence is not None and independence(answer):
        return _withheld(GUARDRAIL_INDEPENDENCE)
    return {"content": answer or "No answer was returned.", "cited_sentence_ids": verified,
            "insufficient_evidence": bool(ans.insufficient_evidence), "guardrail": None}

# ---------- payloads ----------
def resolve_references(content: str, node_refs: Dict[str, int], rels: Dict[int, Dict], paper_ids: Set[str]) -> Dict:
    out: Dict[str, Dict] = {}
    for tok in _unique(TOKEN_RE.findall(content or "")):
        if tok == "q":
            out[tok] = {"kind": "question", "id": "q", "node_id": "q", "missing": False}
        elif tok.startswith("paper:"):
            pid = tok.split(":", 1)[1]
            out[tok] = {"kind": "paper", "id": pid, "node_id": tok, "ref": node_refs.get(tok),
                        "missing": pid not in paper_ids}
        elif tok.startswith("claim:"):
            cid = int(tok.split(":", 1)[1])
            out[tok] = {"kind": "claim", "id": cid, "node_id": tok, "ref": node_refs.get(tok),
                        "missing": tok not in node_refs}
        else:
            rid = int(tok.split(":", 1)[1])
            r = rels.get(rid)
            out[tok] = {"kind": "relationship", "id": rid, "node_id": tok, "missing": r is None,
                        "refs": [node_refs.get(f"claim:{r['claim_a']}"), node_refs.get(f"claim:{r['claim_b']}")] if r else None}
    return out

def message_payloads(conn, rows: List[Dict]) -> List[Dict]:
    """API shape for stored messages. Quotes are rebuilt from `sentences`; references from `node_refs`."""
    node_refs = db.node_ref_map(conn)
    rels = {r["id"]: r for r in db.all_relationships(conn)}
    paper_rows = db.all_papers(conn)
    papers = {p["id"]: paper_detail(p) for p in paper_rows}
    authors: Optional[AuthorIndex] = None
    claim_paper: Dict[int, str] = {}
    rel_claims: Dict[int, Tuple[int, int]] = {}
    if any(m["role"] == "assistant" for m in rows):
        authors = AuthorIndex(paper_rows, db.page1_sentences(conn, list(papers)))
        claim_paper = {c["id"]: c["paper_id"] for c in db.all_claims(conn)}
        rel_claims = {rid: (r["claim_a"], r["claim_b"]) for rid, r in rels.items()}
    sentences = db.sentences_by_ids(conn, [s for m in rows for s in (m.get("cited_sentence_ids") or [])])
    out = []
    for m in rows:
        cited = []
        for sid in m.get("cited_sentence_ids") or []:
            s = sentences.get(sid)
            if s:  # a sentence whose paper was later removed is simply not shown
                cited.append({**quote(sid, s), "paper_title": papers.get(s["paper_id"], {}).get("title")})
        out.append({
            "id": m["id"], "session_id": m["session_id"], "role": m["role"],
            "content": strip_inline_citations(m["content"]) if m["role"] == "assistant" else m["content"],
            "context": m.get("context"), "insufficient_evidence": m["insufficient_evidence"],
            "guardrail": m.get("guardrail"), "question": m.get("question"), "created_at": m["created_at"].isoformat() if m.get("created_at") else None,
            "cited": cited,
            "evidence_removed": bool(m.get("evidence_removed")),
            "removed_evidence": m.get("removed_evidence") or [],
            "references": resolve_references(m["content"], node_refs, rels, set(papers)) if m["role"] == "assistant" else {},
            "integrity": _integrity(m, authors, claim_paper, rel_claims) if m["role"] == "assistant" else None,
        })
    return out

def _integrity(m: Dict, authors: Optional[AuthorIndex], claim_paper: Dict, rel_claims: Dict) -> Optional[Dict]:
    """Re-checks a STORED answer against today's author information. The stored text is never edited; a problem
    is reported as a prominent status shown before the answer."""
    if m.get("guardrail") or authors is None:
        return None
    issues = independence_issues(m["content"], referenced_papers(m["content"], claim_paper, rel_claims), authors)
    if not issues:
        return None
    return {"headline": REVIEW_HEADLINE,
            "detail": " ".join(_unique([i["detail"] for i in issues])),
            "issues": issues,
            "note": "Checked by SpiderMonkey when displayed; the answer text below is unchanged."}

# ---------- public API ----------
def history(session_id: Optional[str] = None) -> List[Dict]:
    sid = check_session(session_id)
    with db.connect() as conn:
        return message_payloads(conn, db.chat_messages(conn, sid))

def ask(llm: LLM, question: str, message: str, context: Optional[Dict] = None,
        session_id: Optional[str] = None) -> Dict:
    """Answer one user message. Both messages are stored only after the answer is validated."""
    sid = check_session(session_id)
    text = check_message(message)
    ctx_item = check_context(context)
    with db.connect() as conn:
        # Only turns asked under the current question reach the model (a changed question is a new frame).
        ensure_context_exists(conn, ctx_item)
        # Turns whose evidence was removed (a paper was removed) never reach the model again.
        earlier = db.chat_messages(conn, sid, limit=HISTORY_TURNS, question=question, for_model=True)
        try:
            ctx = gather_context(conn, llm, text, ctx_item)
        except Exception as exc:  # embedding call failed
            raise ChatUnavailableError(f"Gemini was unavailable ({type(exc).__name__}). "
                                       "Your message was not saved; please try again.") from exc
    prompt, shown, tokens = build_chat_prompt(question, text, ctx, earlier)
    try:
        ans = llm.generate_json(prompt, ChatAnswer)
    except Exception as exc:
        raise ChatUnavailableError(f"Gemini was unavailable ({type(exc).__name__}). "
                                   "Your message was not saved; please try again.") from exc
    claim_paper = {c["id"]: c["paper_id"] for c in ctx["claims"]}
    rel_claims = {r["id"]: (r["claim_a"], r["claim_b"]) for r in ctx["relationships"]}
    result = validate_answer(ans, shown, tokens, independence=lambda a: independence_issues(
        a, referenced_papers(a, claim_paper, rel_claims), ctx.get("authors")))
    if result["guardrail"]:
        log.warning("chat answer withheld (%s)", result["guardrail"])
    with db.connect() as conn:
        user_row = db.insert_chat_message(conn, {"session_id": sid, "role": "user", "content": text, "context": ctx_item,
                                                 "question": question})
        bot_row = db.insert_chat_message(conn, {"session_id": sid, "role": "assistant", "context": ctx_item,
                                                "question": question, **result})
        user_msg, bot_msg = message_payloads(conn, [user_row, bot_row])
    retrieved = {"claims": [c["id"] for c in ctx["claims"]], "relationships": [r["id"] for r in ctx["relationships"]],
                 "impacts": len(ctx["impacts"])}
    return {"session_id": sid, "user": user_msg, "assistant": bot_msg, "retrieved": retrieved}