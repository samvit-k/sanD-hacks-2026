# ResearchLens — Reconciled Project Proposal

## Unique Value Proposition
ResearchLens turns scientific papers into a living **claim-level evidence graph** that shows where findings agree, genuinely contradict one another, or remain unresolved. Unlike a paper summarizer or citation map, it evaluates **how a new study changes an existing body of evidence**, with every important connection traceable to a passage in the original research. A visual graph and evidence-grounded chatbot let researchers investigate those changes rather than accepting a paper's conclusions at face value.

## Reconciled Direction
**Approximately 65% of the research-library plan + 35% of the question-first architecture plan.** Keep the distinctive workflow of uploading a collection and then evaluating a new paper; add question-led paper discovery, more rigorous claim matching, and the PDF plan's visual layout. Focus on **one research question**, not one journal or publisher. The graph must connect **individual claims and evidence**; connections between entire papers are only visual summaries of those claim-level relationships.

## Full Workflow

1. **Start a research workspace.** The user enters a question (for example, *Do AI tutors improve learning?*) and uploads a few relevant scientific PDFs. Alternatively, the system can suggest papers for that question. The uploaded library is the starting evidence base, not assumed to be complete or correct.

2. **Find and ingest relevant studies.** Search **OpenAlex, Crossref, and Europe PMC** first for academic metadata, citations, and open-access links. Use **Bright Data's Google Scholar Scraper API** to discover additional papers and citation neighbors; resolve its results against DOI/title metadata and remove duplicates. Use **Bright Data Web Unlocker** only when useful to retrieve permitted, publicly accessible open-access pages or correction/retraction notices that scholarly APIs do not provide. Record the DOI, source URL, publication details, license when available, and whether the evidence is **full text or abstract only**. Scholar results and citation counts are discovery signals, not proof of research quality.

3. **Extract precise claims with Gemini.** For each paper, identify a few testable findings. Preserve the **exact supporting quotation and page/section**, plus key study conditions: population, sample size, method, duration, measured outcome, significance, effect size where available, and limitations. **Validate in code that each quotation appears in the retrieved text;** reject or flag claims that fail. Do not treat an AI paraphrase as original evidence.

4. **Match claims before comparing them.** Use embeddings and structured fields to find claims about the same hypothesis and outcome. Check whether populations, methods, durations, and measurements are comparable. Similar wording alone is not enough to establish a meaningful comparison.

5. **Verify relationships and flag gaps.** Gemini examines matched claims **against their original passages and relevant external evidence**, not just graph labels. Label relationships as **supporting/compatible**, **genuinely contradictory** (comparable studies with incompatible results), **apparently contradictory** (differences plausibly explained by study conditions), **related but not directly comparable**, or **unresolved**. Flag stated limitations, weakly supported claims, and **gaps in the available collection** separately; an unrepresented topic is not necessarily a gap in all scientific research. A statistically nonsignificant finding does not automatically refute a significant one. Show when a result is based only on an abstract, and require cited passages for each judgment.

6. **Save and visualize the evidence graph.** Store papers, claim nodes, evidence passages, embeddings, and verified relationships in **Tiger Data (PostgreSQL + pgvector)**. Display an interactive radial graph centered on the research question: papers nearer the center are more relevant, and clicking a paper reveals its individual claims and evidence links. Use **solid versus dashed edges** for genuine versus apparent contradictions, plus distinct styles for supporting, related, and unresolved relationships. Clicking an edge opens the two source quotations and the study conditions side by side. Mark abstract-only sources visibly. Relevance is a navigation aid, **not a truth score**.

7. **Evaluate a new paper and ask questions.** When a user uploads another paper, repeat extraction and comparison against the existing graph and any needed outside evidence. Highlight which previous findings it **strengthens, challenges, qualifies, or leaves unanswered**, then update the graph. A **GraphRAG chatbot** follows relevant claim connections, retrieves the actual passages, and answers questions with citations and clear uncertainty—not merely from graph labels.

## Example Walkthrough — Do AI Tutors Improve Learning?

*All papers and outcomes below are fictional examples.*

- **Initial library:** Paper A reports **25% higher test scores after 12 weeks**; Paper B finds **no statistically significant improvement after 2 weeks**; Paper C finds stronger gains among students who began with lower scores.
- **Extraction and matching:** Gemini captures the claim and exact passage from each paper. The graph links them to the shared topic, but **does not automatically label A and B contradictory**: different durations and participant groups may explain the outcomes.
- **External check:** OpenAlex and Europe PMC locate related research; Bright Data's Google Scholar scraper can discover additional relevant papers, while Web Unlocker can retrieve a permitted open-access source page when needed. The agent resolves source identity, checks the original passages, and does not assume that many similar papers constitute independent confirmation.
- **New study:** The user adds Paper D, which reports that short-term gains are **not retained six months later**. ResearchLens links D to A's short-term result and flags **long-term retention as unresolved in the current library**, rather than declaring A false.
- **Visual investigation and chat:** The researcher clicks the A–D relationship to view their results and methods, then asks, *"Does Paper D disprove Paper A?"* The chatbot replies: *"Not necessarily. A measured short-term test scores; D measured whether improvements lasted. Both results could hold."* It points to the relevant quoted passages.

## Why It Matters
Scientific findings are easy to overgeneralize, especially outside one's own area of expertise. ResearchLens encourages **evidence-based trust calibration**, inspired by the Gell-Mann Amnesia Effect: researchers can examine where their confidence is warranted, where methods explain conflicting results, and where evidence is incomplete. The aim is **not** to assign a universal credibility score to a journal or automatically declare scientific claims true or false.


**Source-access guardrails:** Use public, permitted material only; respect site terms, access restrictions, and open-access licenses. Never bypass a login or paywall. Label abstract-only evidence, keep citations to original passages, and record retrieval and publication details so findings remain auditable. Bright Data adds discovery and access to public pages, not scientific verification.

**Database decision:** **Tiger Data can replace Neon completely for this project.** Both provide PostgreSQL and support vector search with `pgvector`; tables can store graph edges without Neo4j. Tiger Data also supports additional search capabilities, but **it does not create the knowledge graph or perform verification automatically**—Gemini and your application implement those steps. Choose **one** database, not both; use Tiger Data if it is easier to access or earns relevant sponsor recognition. Neon is not required.

---

## Hackathon Build Scope (5 hours — hard deadline)

*The vision above is unchanged. This section defines what we actually build in 5 hours. Where they differ, **this section governs the build**; everything else is post-hackathon.*

### MVP (what ships)
1. **One research question, 3–5 preloaded open-access papers**, plus one "new" Paper D for the live demo. Choose papers with an obvious tension (e.g., different duration or population).
2. **Gemini claim extraction** with sentence-ID quote verification.
3. **Top-k similarity matching**, then evidence-backed claim comparison by Gemini.
4. **Tiger Data (PostgreSQL + pgvector)** as the single datastore.
5. **Interactive Cytoscape graph**: click claims and edges to inspect original quotations, study conditions, and explanations.
6. **New-paper impact analysis** (the differentiator): which earlier findings Paper D **strengthens, challenges, qualifies, or leaves unresolved**.
7. **One Bright Data workflow** (Google Scholar Scraper API), cached, with an OpenAlex fallback.
8. **Stretch only:** GraphRAG chatbot, after the graph and impact analysis work reliably.

**Out of scope for the hackathon:** Crossref/Europe PMC fan-out, Web Unlocker, external-evidence verification, auth, deployment beyond running locally, Redis/background workers, a separate graph database.

### Stack
FastAPI (synchronous, no workers) · PyMuPDF · Gemini (`google-genai`) · Tiger Data via psycopg + pgvector · Vite + React + Cytoscape.js.

### Technical rules
- **Sentence-ID quotes (globally unique).** PyMuPDF extracts text per page. Each sentence gets the ID `{paper_id}:p{page}_s{n}` (e.g., `paperA:p4_s12`), which is the primary key of `sentences`, with `paper_id`, `page`, and `idx` also stored as columns. The prompt shows sentences with their full IDs, and Gemini cites **full sentence IDs, never quote text**. Code rebuilds every quote from the DB.
- **Citation validation.** A cited ID is valid only if it exists in `sentences` **and** its `paper_id` belongs to the claim(s) being judged. For extraction, every ID must come from the paper being extracted; a claim with no valid IDs is dropped. For pair comparisons and impacts, valid IDs must include at least one sentence from **each** of the two papers. Check this using the stored `paper_id`, not by parsing the ID prefix. Quotes are always verbatim; page numbers always come from the PDF.
- **Limited coverage first.** Extract **2–3 important testable claims per paper** (`max_claims`). Match each claim to its **top 2** nearest claims from other papers (`top_k`), skip cosine similarity < ~0.6, remove duplicate pairs. Raise these limits only after Checkpoint 2 passes.
- **Embeddings:** `gemini-embedding-001` with `output_dimensionality=768` → `vector(768)` (pgvector HNSW indexes only up to 2,000 dims).
- **Conditions inform, never decide.** Code compares population, sample size, method, duration, and outcome measure into a `condition_diff` (missing → `"not reported"`). It never assigns a label. Differing conditions don't automatically mean "apparent contradiction".
- **Gemini judges each pair** from both claims, their source sentences, and the `condition_diff`. It returns `label` ∈ {`supports`, `genuine_contradiction`, `apparent_contradiction`, `not_comparable`, `unresolved`}, an `explanation`, `key_differences`, and `cited_sentence_ids` from **both** papers. A nonsignificant result doesn't refute a significant one.
- **Impact is judged separately, not mapped from labels.** For each earlier claim matched by Paper D, a separate Gemini call weighs the original evidence and study conditions (with the relationship result as context only) and returns `impact` ∈ {`strengthens`, `challenges`, `qualifies`, `leaves_unresolved`}, a `rationale`, and `cited_sentence_ids` from both papers. Unmatched earlier claims are listed as "not addressed by the new paper".
- **Citation guardrail.** If validation fails (unknown IDs, IDs from the wrong paper, or no sentence from one of the two papers), force `unresolved` / `leaves_unresolved` and show "insufficient evidence".
- **Bright Data: one workflow ("Find related research").** Send Paper D's title or main claim to the **dedicated Google Scholar Scraper API** (async: `POST /datasets/v3/trigger?dataset_id=…` → poll progress → download snapshot). **Before writing the parser**, get the Scholar `dataset_id` and output fields from Bright Data's Scraper Library, run one test trigger, and save the raw output to `backend/fixtures/brightdata_scholar_sample.json`. Resolve results against OpenAlex (DOI/title) and remove duplicates. Show them as "external discovery" nodes: **leads, not evidence**. Cache results in `discovery_cache` and pre-run the demo query. If Bright Data fails or isn't configured, fall back to OpenAlex `/works?search=` and label the source. Public, permitted material only.
- **Graph UI:** Cytoscape `concentric` layout with the question at the center, papers in the first ring, claims around their papers. Edges: supports = solid green; genuine contradiction = thick solid red; apparent contradiction = dashed orange/red; unresolved = dotted gray; not comparable = thin light gray. Clicking an edge shows both quotes side by side, the condition diff, and the explanation, labeled "Model assessment, not a verdict". Abstract-only papers get a badge.

### Research question
The user enters the research question in the frontend (`PUT /api/workspace`). It is stored in the single-row `workspace` table and drives extraction, comparison, impact analysis and graph relevance. There is one active question at a time. Changing it while analysis exists requires an explicit reset, so analyses made under different questions are never mixed. `RESEARCH_QUESTION` in `.env` is only an optional demo/testing default.

### Minimal API
See `backend/API.md` for full contracts. `GET/PUT /api/workspace` · `POST /api/papers` (`mode=library` builds the initial evidence library; `mode=evaluate`, the default, runs new-paper impact analysis) · `GET /api/graph` · `GET /api/claims/{id}` · `GET /api/relationships/{id}` · `GET /api/impact/latest` · `POST /api/discover` · `GET /api/checks` · (stretch) `POST /api/chat`

### Schema (Tiger Data)
- `papers`: id text PK, title, doi, url, year, access_level (full|abstract), license, is_new
- `sentences`: id text PK (`{paper_id}:p{page}_s{n}`), paper_id FK → papers, page, idx, text; unique (paper_id, page, idx)
- `claims`: id, paper_id FK, text, conditions jsonb, sentence_ids text[], embedding vector(768)
- `relationships`: id, claim_a, claim_b, label, condition_diff jsonb, explanation, cited_sentence_ids text[]
- `impacts`: id, new_claim_id, prior_claim_id, impact, rationale, cited_sentence_ids text[]
- `discovery_cache`: query_hash PK, source, results jsonb, created_at

### Layout
`backend/app/{main,ingest,extract,compare,discover,db}.py` · `backend/seed.py` · `backend/schema.sql` · `backend/fixtures/` · `frontend/` (Vite React: `GraphView.jsx`, `Inspector.jsx`, `ImpactPanel.jsx`, `fixtures/graph.json`) · `eval/gold_pairs.json`

### Five-hour schedule (roles: A ingest/extract · B compare/impact · C DB/API/Bright Data · D frontend)
| Time | Milestone |
|---|---|
| 0:00–0:30 | **Everyone:** choose papers + Paper D; agree on JSON contracts (claim, relationship, graph, impact) and save them as fixtures. C: Tiger Data + `schema.sql` + Bright Data sample. D: render `fixtures/graph.json`. |
| 0:30–1:45 | A: sentence IDs + extraction. B: comparison on hard-coded claims. C: DB queries + read endpoints + discovery/cache. D: edge styles + inspector panels (mock data). |
| 1:45–2:15 | ✅ **Checkpoint 1:** `seed.py` fills the DB end to end; UI reads `GET /graph`. |
| 2:15–3:30 | A: tune extraction. B: impact prompt + `POST /papers`. C: `/discover` fallback + precompute `paper_d_result.json`. D: impact panel, upload, highlighting, discovery nodes. |
| 3:30–4:00 | ✅ **Checkpoint 2 / feature freeze:** manual review of claims and gold pairs; time the live upload. |
| 4:00–4:30 | Raise limits and re-seed if stable → then stretch chat. Fix mislabeled pairs. Snapshot fallbacks. Demo script. |
| 4:30–5:00 | Buffer, rehearse twice, submit. |

**Dependencies:** contracts by 0:30 (frontend never waits on the backend) · sentence IDs before real comparisons · schema before seeding.

**Cut order if behind (cut from the top first):** (1) GraphRAG chat → (2) live Bright Data, keeping cached results → (3) discovery nodes, using a list instead → (4) live Paper D upload, loading the precomputed fixture through the same UI → (5) relevance-ordered rings → (6) conditions reduced to population, duration, outcome measure.

**Never cut:** sentence-ID verification, the edge inspector with both quotes, the impact panel.

### Validation criteria
1. **Quote integrity:** every claim has at least one `sentence_id`; every ID exists **and** belongs to that claim's paper (an SQL check returns 0 violations); spot-check 5 claims against the PDFs.
2. **Gold set:** 8–10 manually labeled claim pairs in `eval/gold_pairs.json`, including at least one pair with different conditions but compatible results. Target ≥ 7/10 agreement with Gemini.
3. **No unsupported conclusions:** every displayed edge and impact cites valid sentences from both papers, or appears as "unresolved / insufficient evidence". UI copy never says "true", "false", "disproves" or "debunked". Abstract-only papers are badged.
4. **Impact correctness:** the team agrees in advance on Paper D's expected impacts; Gemini's judgments match on the demo-critical claims.
5. **Demo resilience:** the demo works with Bright Data offline (cache) and with the live Gemini call skipped (Paper D fixture).
