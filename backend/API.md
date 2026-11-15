# ResearchLens API (MVP)

Base URL in development: `http://localhost:8000` (Vite proxies `/api` from `:5173`). All bodies are JSON except uploads, which are `multipart/form-data`.

## Frontend flow

1. `GET /api/workspace`: is there a question? Is there a library (`has_library`)?
2. `PUT /api/workspace`: set the user's research question.
3. `POST /api/papers` with `mode=library`, once per paper (3–5 papers). Reload `GET /api/graph` after each upload.
4. `POST /api/papers` with `mode=evaluate` for the new paper → impact panel + `GET /api/graph`.
5. Optional: `POST /api/discover` → related-research leads. They appear in `GET /api/graph` as `discovery` nodes.

There is **one active research question**. All analysis in the database was produced under it. Changing it requires an explicit `reset: true`, which deletes the existing analysis (409 otherwise).

Uploads are synchronous. A library upload makes about 2 + k Gemini calls; an evaluation makes about 2 + 2k. Allow 30–90 s, and use the precomputed fixtures as a fallback (see below).

---

## Workspace

### `GET /api/workspace`
```json
{
  "question": "Do AI tutors improve learning?",
  "source": "workspace",
  "is_default": false,
  "counts": {"papers": 4, "library_papers": 3, "evaluated_papers": 1, "claims": 4,
             "library_claims": 3, "relationships": 5, "impacts": 2},
  "has_library": true
}
```
- `source`: `"workspace"` (set by the user), `"default"` (from `RESEARCH_QUESTION` in `.env`, demo/testing only), or `null` (no question; the UI must ask for one).
- `has_library`: true when at least one library paper has verified claims. Enable "Evaluate a new paper" only when this is true.

### `PUT /api/workspace`
Request:
```json
{"question": "Do AI tutors improve learning?", "reset": false}
```
Whitespace is trimmed and collapsed. The question must be 1–300 characters.

| Situation | Result |
|---|---|
| No analysis yet | `200`, question stored |
| Same question as current (even with `reset: true`) | `200`, nothing deleted, `"reset": false` |
| Different question, analysis exists, `reset: false` | `409` (below). Nothing changes. |
| Different question, analysis exists, `reset: true` | `200`, `"reset": true`. Papers, sentences, claims, relationships, impacts and discovery links are deleted, and the new question is stored, all in one transaction. The discovery cache is kept. |
| Empty or over 300 characters | `422` `{"detail": "The research question must not be empty."}` |

200 response: the `GET /api/workspace` body plus `"reset": true|false`.

409 response (show a "Start a new evidence graph? This deletes N papers…" confirmation, then resend with `reset: true`):
```json
{"detail": {
  "message": "Existing analysis was produced under a different research question. Resend with reset=true to delete it and start a new evidence graph.",
  "current_question": "Do AI tutors improve learning?",
  "counts": {"papers": 4, "library_papers": 3, "evaluated_papers": 1, "claims": 4,
             "library_claims": 3, "relationships": 5, "impacts": 2}}}
```

---

## Uploads: `POST /api/papers` (multipart)

| Field | Required | Notes |
|---|---|---|
| `file` | yes | PDF, at most 30 MB, must contain extractable text |
| `mode` | no | `"library"` or `"evaluate"` (**default `"evaluate"`**, kept for backward compatibility; the frontend should always send it) |
| `title` | no | Guessed from the PDF if omitted |
| `doi`, `url`, `year` | no | Stored as paper metadata |

The same file uploaded again gets the same paper ID and replaces the earlier version.

```bash
curl -F mode=library -F title="AI tutor RCT" -F file=@a.pdf http://localhost:8000/api/papers
curl -F mode=evaluate -F title="Retention follow-up" -F file=@d.pdf http://localhost:8000/api/papers
```

### `mode=library`: add to the initial evidence library
Ingests the paper, extracts 2–3 claims (strict sentence-ID validation), and compares them with the existing library claims (top-k). No impact analysis.
```json
{
  "mode": "library",
  "question": "Do AI tutors improve learning?",
  "paper": {"id": "ai-tutor-rct-12-weeks-285403", "title": "AI tutor RCT, 12 weeks", "doi": null, "url": null,
            "year": null, "access_level": "full", "license": null, "is_new": false, "relevance": 0.71},
  "sentences": 214,
  "claims_kept": 3,
  "extraction": {"returned": 3, "kept": 3, "rejected_invalid_citations": 0, "rejected_empty_text": 0,
                 "rejected_over_limit": 0},
  "relationships_created": 2,
  "library_papers": 3,
  "warning": null,
  "timings_s": {"ingest": 0.4, "extract": 9.8, "compare": 21.3}
}
```
`warning` is `"No verifiable claims were extracted from this paper."` when `claims_kept` is 0. The paper stays in the library but adds no claims to the graph.

### `mode=evaluate`: impact of a new paper on the existing library
The same response as before, plus `mode` and `question`:
```json
{
  "mode": "evaluate",
  "question": "Do AI tutors improve learning?",
  "status": "live",
  "paper": {"id": "retention-of-ai-tutor-gains-eb9754", "title": "Retention of AI tutor gains", "is_new": true, "...": "..."},
  "new_claims": [{"id": 4, "text": "...", "quotes": [], "conditions": {}, "...": "..."}],
  "counts": {"strengthens": 0, "challenges": 0, "qualifies": 2, "leaves_unresolved": 0, "not_addressed": 1},
  "impacts": [{
    "id": 1, "impact": "qualifies", "insufficient_evidence": false, "relationship_id": 4,
    "rationale": "D does not dispute A's immediate gain; it shows the gain was not detectable six months later.",
    "prior_claim": {"id": 2, "paper_id": "...", "paper_title": "...", "access_level": "full",
                    "text": "...", "conditions": {"duration": "2 weeks", "...": null},
                    "quotes": [{"id": "paper-b:p1_s2", "paper_id": "...", "page": 1, "section": "results",
                                "text": "..."}]},
    "new_claim": {"...": "same shape as prior_claim"},
    "cited": [{"id": "...", "paper_id": "...", "page": 1, "section": "results", "text": "..."}]
  }],
  "not_addressed": [{"...": "claim detail of earlier claims the new paper did not match"}],
  "extraction": {"returned": 2, "kept": 2, "rejected_invalid_citations": 0, "...": 0},
  "timings_s": {"ingest": 0.3, "extract": 8.1, "compare": 19.0, "impact": 31.2}
}
```
- `impact` ∈ `strengthens | challenges | qualifies | leaves_unresolved`, each a separate Gemini judgment.
- `insufficient_evidence: true` means the model's citations failed validation. In that case `rationale` is the generic insufficient-evidence message, and `cited` is `[]`.

### Upload errors
| Status | When | `detail` |
|---|---|---|
| 409 | No active question | `"Set a research question first (PUT /api/workspace)."` |
| 409 | `mode=evaluate` before any library claims exist | `"There is no evidence library to evaluate against yet. Upload 3-5 library papers (mode=library) first."` |
| 422 | PDF has no extractable text / not a PDF | `"No extractable text found in this PDF. …"` |
| 422 | Bad `mode` | `"mode must be one of ['library', 'evaluate']"` |
| 413 | Over 30 MB | |
| 503 | `GEMINI_API_KEY` or `DATABASE_URL` missing | |

In every error case, nothing is stored.

---

## Graph and inspectors

### `GET /api/graph`
Cytoscape elements plus every detail the inspectors need (no second request needed):
```json
{
  "question": "Do AI tutors improve learning?",
  "meta": {"source": "live"},
  "nodes": [
    {"data": {"id": "q", "type": "question", "label": "Do AI tutors improve learning?"}},
    {"data": {"id": "paper:paper-a", "type": "paper", "label": "AI tutor RCT, 12 weeks", "paper_id": "paper-a",
              "access_level": "full", "is_new": false, "relevance": 0.71}},
    {"data": {"id": "claim:1", "type": "claim", "label": "Tutor students' test scores rose 25 percent…",
              "claim_id": 1, "paper_id": "paper-a", "paper_node": "paper:paper-a", "is_new": false}},
    {"data": {"id": "disc:0", "type": "discovery", "label": "Long-term effects of…", "anchor": "paper:paper-d", "index": 0}}
  ],
  "edges": [
    {"data": {"id": "about:paper-a", "source": "q", "target": "paper:paper-a", "type": "question_paper", "relevance": 0.71}},
    {"data": {"id": "has:1", "source": "paper:paper-a", "target": "claim:1", "type": "has_claim"}},
    {"data": {"id": "rel:1", "source": "claim:1", "target": "claim:2", "type": "relationship",
              "label": "apparent_contradiction", "relationship_id": 1, "insufficient_evidence": false}},
    {"data": {"id": "found:0", "source": "paper:paper-d", "target": "disc:0", "type": "discovered"}}
  ],
  "papers": {"paper-a": {"id": "paper-a", "title": "...", "access_level": "full", "is_new": false, "relevance": 0.71, "...": "..."}},
  "claims": {"1": {"id": 1, "text": "...", "conditions": {}, "quotes": [{"id": "...", "page": 1, "section": "results", "text": "..."}]}},
  "relationships": {"1": {
    "id": 1, "label": "apparent_contradiction", "similarity": 0.83, "insufficient_evidence": false,
    "explanation": "...", "key_differences": ["duration"],
    "condition_diff": {"duration": ["12 weeks", "2 weeks"], "population": "same", "method": "not reported",
                       "sample_size": "not reported", "outcome_measure": "same"},
    "claim_a": {"...": "claim detail"}, "claim_b": {"...": "claim detail"},
    "cited": {"a": [{"...": "quote"}], "b": [{"...": "quote"}]}}},
  "discovery": null
}
```
- Edge `type`: `question_paper` (radial navigation only, not a scientific relationship), `has_claim`, `relationship`, `discovered`.
- Relationship `label` ∈ `supports | genuine_contradiction | apparent_contradiction | not_comparable | unresolved`.
- `condition_diff` per field: `"same"`, `"not reported"`, or `[value_a, value_b]`. It is aligned with `claim_a` / `claim_b`.
- `relevance` is a navigation aid (similarity to the question), **not a truth score**.
- With no question set: `"question": null`, the question node is labelled `"No research question set"`, and there are no other nodes or edges.

### Other read endpoints
- `GET /api/claims/{id}`, `GET /api/relationships/{id}`: the same objects as in `graph.claims` / `graph.relationships` (404 if unknown).
- `GET /api/impact/latest`: the evaluate-mode summary for the most recently evaluated paper (404 if none).
- `GET /api/checks`: `{"ok": true, "violations": {...}}`, the citation-integrity checks.
- `GET /api/health`: `{"database": bool, "gemini": bool, "brightdata": bool, "question": "...", "max_claims": 3, "top_k": 2}`.

### `POST /api/discover`
`{}` uses the latest evaluated paper's title; `{"paper_id": "..."}` or `{"query": "..."}` also work.
```json
{"query": "Retention of AI tutor gains", "source": "brightdata", "cached": false, "fallback_reason": null,
 "for_paper_id": "retention-of-ai-tutor-gains-eb9754",
 "results": [{"title": "...", "year": 2021, "doi": "10....", "openalex_id": "https://openalex.org/W…",
              "oa_url": "https://…", "abstract": "...", "access_level": "abstract", "resolved": true}]}
```
`source` is `"openalex"` when Bright Data is unavailable or not configured; `fallback_reason` says why. Results are discovery leads, not evidence.

---

## Demo fallback fixtures (`frontend/public/fixtures/`)
`graph.json` (library only), `graph_with_new.json` and `paper_d_result.json` (`status: "precomputed"`) have the same shapes as above. They are currently **fictional samples**. Replace them with real output by running `scripts/snapshot.py --stage before` and `--stage after`.
