# SpiderMonkey

**A claim-level evidence graph for scientific papers.**

SpiderMonkey reads a set of research papers on a single question and maps where their findings agree, where they genuinely contradict each other, and where the question is still open. When you add a new paper, it shows which earlier findings the new paper **strengthens, challenges or qualifies**, and which it **leaves unresolved**. Every connection is traced back to verbatim sentences in the original PDFs.

**Live demo:** [sand-hacks-2026.vercel.app](https://sand-hacks-2026.vercel.app)

> SpiderMonkey's labels are model assessments, not verdicts. The goal is to help researchers decide how much to trust a finding, not to declare findings true or false.

---

## How it works

1. **Ask a question.** Enter a research question, or let SpiderMonkey suggest one from the papers you upload.
2. **Build a library.** Upload PDFs. Each page is split into sentences, and each sentence gets a stable ID of the form `{paper_id}:p{page}_s{n}`.
3. **Extract claims.** Gemini identifies a few testable findings in each paper, along with study conditions (population, sample size, method, duration, outcome measure). Gemini cites sentence IDs rather than quoting text. The code rebuilds every quote from the database and drops claims whose citations don't come from that paper.
4. **Match, then compare.** Claim embeddings (pgvector) find the nearest claims in other papers. For each candidate pair, the code computes a `condition_diff`, and Gemini assigns one of these labels: `supports`, `genuine_contradiction`, `apparent_contradiction`, `not_comparable` or `unresolved`. The judgment must cite sentences from **both** papers, or it falls back to *unresolved / insufficient evidence*.
5. **Evaluate a new paper.** A separate impact pass labels each earlier claim the new paper matches as `strengthens`, `challenges`, `qualifies` or `leaves_unresolved`. Earlier claims the new paper doesn't match are listed as *not addressed*.
6. **Explore.** An interactive graph shows the question at the center, papers around it and claims around each paper. Click an edge to see both source quotations side by side with the differences in study conditions. A grounded assistant answers questions with citations to the underlying passages.
7. **Discover related research.** A Bright Data Google Scholar workflow, with OpenAlex as the fallback, suggests related papers. They appear as *leads*, not evidence.

### Edge legend

| Relationship | Style |
|---|---|
| Supports / compatible | solid green |
| Genuine contradiction | thick solid red |
| Apparent contradiction (explained by study conditions) | dashed orange |
| Unresolved | dotted gray |
| Not comparable | thin light gray |

Papers available only as abstracts are badged in the UI.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, Cytoscape.js, d3-force |
| Backend | FastAPI, PyMuPDF, Pydantic |
| AI | Google Gemini (`google-genai`): generation and `gemini-embedding-001` (768-dim) |
| Database | Tiger Data (PostgreSQL + pgvector) |
| Discovery | Bright Data Google Scholar Scraper API, OpenAlex fallback |
| Hosting | Vercel (frontend and backend services) |

---

## Getting started

### Prerequisites

- Python 3.12
- Node.js 18 or later
- A PostgreSQL database with the `pgvector` extension (for example, [Tiger Data](https://www.tigerdata.com/))
- A Gemini API key
- *(Optional)* A Bright Data API token and the Google Scholar Scraper dataset ID

### 1. Configure the environment

Create a `.env` file in the repository root:

```ini
# Required
DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
GEMINI_API_KEY=your-gemini-key

# Optional (defaults shown)
GEMINI_MODEL=gemini-3.5-flash
GEMINI_EMBED_MODEL=gemini-embedding-001
RESEARCH_QUESTION=             # demo default only; the app stores the real question
MAX_CLAIMS=3                   # claims extracted per paper
TOP_K=2                        # nearest claims compared per claim
SIM_THRESHOLD=0.6              # minimum cosine similarity for a candidate pair

# Optional: Bright Data discovery (falls back to OpenAlex when unset)
BRIGHTDATA_API_TOKEN=
BRIGHTDATA_SCHOLAR_DATASET_ID=
BRIGHTDATA_SCHOLAR_INPUT={"keyword": "{query}"}
BRIGHTDATA_LIVE_WAIT_SECONDS=45
```

Never commit `.env`; it is listed in `.gitignore`.

### 2. Run the backend

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python seed.py --init           # apply the database schema (idempotent)
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Check that it's running at `http://localhost:8000/api/health`.

### 3. Run the frontend

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to the backend on port 8000.

### Seeding a library from the command line (optional)

Instead of uploading through the UI, put PDFs and a `manifest.json` in `data/papers/`, then run:

```bash
cd backend
.venv/bin/python seed.py                                        # ingest manifest papers and compare them
.venv/bin/python seed.py --new data/papers/d.pdf --title "..."  # evaluate a new paper
```

Run `.venv/bin/python seed.py --help` for the remaining options (`--refresh`, `--reextract` and `--reset`).

---

## Testing

```bash
# Backend: uses an embedded PostgreSQL + pgvector (pgserver) and a fake LLM; no API keys needed
cd backend && .venv/bin/python -m pytest -q

# Frontend
cd frontend && npm test
```

`backend/scripts/eval_gold.py` measures how often Gemini's labels agree with the hand-labeled claim pairs in `eval/gold_pairs.json`.

---

## Project structure

```
backend/
  app/
    main.py        FastAPI routes
    ingest.py      PDF parsing and sentence IDs
    extract.py     Gemini claim extraction
    compare.py     Matching, relationship judging, impact analysis
    citations.py   Sentence-ID validation
    graph.py       Graph payload for the UI
    chat.py        Grounded research assistant
    discover.py    Bright Data / OpenAlex discovery
    db.py          PostgreSQL + pgvector access, per-session schemas
  schema.sql       Per-session research tables
  seed.py          Command-line seeding and evaluation
  API.md           Full API reference
  tests/
frontend/
  src/
    components/    Graph canvas, inspector, assistant, workspace panels
    api/           Backend client
eval/
  gold_pairs.json  Hand-labeled claim pairs for evaluation
vercel.json        Vercel services and /api routing
```

See [`backend/API.md`](backend/API.md) for request and response contracts.

---

## Design principles

- **Every quote is verbatim.** Models cite sentence IDs, and the code rebuilds every quote and page number from the stored PDF text.
- **Every judgment cites both papers.** A relationship or impact without valid citations from both sides is shown as *insufficient evidence*.
- **Study conditions inform judgments but never decide them.** Different populations or durations don't automatically make a contradiction "apparent".
- **A nonsignificant result is not a refutation.** It doesn't automatically count against a significant one.
- **Discovery results are leads, not evidence.** Search results and citation counts are signals for what to read next, not measures of quality.
- **Only public, permitted sources.** SpiderMonkey never bypasses logins or paywalls.

---

Built at SanD Hacks 2026.
