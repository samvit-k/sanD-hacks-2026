-- ResearchLens PER-SESSION schema (Tiger Data: PostgreSQL + pgvector).
-- Every research session owns one PostgreSQL schema ("rl_<session id>") containing these tables; db.py runs
-- this file with search_path = rl_<id>, public. Shared objects (vector extension, session registry, discovery
-- cache) live in schema_public.sql. Idempotent and non-destructive: safe to re-run (never drops data).

-- The single active research question (MVP: one at a time). The id CHECK allows only one row.
-- All claims, relationships and impacts in the database were analyzed under this question.
CREATE TABLE IF NOT EXISTS workspace (
    id         int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    question   text NOT NULL CHECK (length(btrim(question)) BETWEEN 1 AND 300),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS papers (
    id           text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]*$'),
    title        text NOT NULL,
    doi          text,
    url          text,
    year         int,
    access_level text NOT NULL DEFAULT 'full' CHECK (access_level IN ('full', 'abstract')),
    license      text,
    is_new       boolean NOT NULL DEFAULT false,
    relevance    real,          -- navigation aid only (max claim/question similarity), never a truth score
    created_at   timestamptz NOT NULL DEFAULT now()
);
-- Upload identity and progress: the full PDF content hash identifies "this exact file" (duplicate
-- protection and timeout recovery); analysis_status is 'processing' while an upload is being analyzed.
ALTER TABLE papers ADD COLUMN IF NOT EXISTS content_sha256 text;
ALTER TABLE papers ADD COLUMN IF NOT EXISTS analysis_status text NOT NULL DEFAULT 'complete';
-- 'pending': the paper and its sentences are stored but it has no analysis under the CURRENT question
-- (after a user-confirmed question change that kept the papers). Re-created idempotently so older
-- databases gain the value; existing rows always satisfy it.
ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_analysis_status_check;
ALTER TABLE papers ADD CONSTRAINT papers_analysis_status_check
    CHECK (analysis_status IN ('pending', 'processing', 'complete'));
-- Outcome of the latest claim extraction (counts, outcome, optional model note). NULL = not recorded.
-- Diagnostic only: never evidence, never cited.
ALTER TABLE papers ADD COLUMN IF NOT EXISTS extraction_report jsonb;
CREATE INDEX IF NOT EXISTS papers_content_sha256 ON papers (content_sha256);

-- Sentence IDs are globally unique: '{paper_id}:p{page}_s{n}'.
-- Citation validation always uses the stored paper_id column, never the ID prefix.
CREATE TABLE IF NOT EXISTS sentences (
    id       text PRIMARY KEY,
    paper_id text NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page     int  NOT NULL,
    idx      int  NOT NULL,
    section  text NOT NULL DEFAULT 'front',  -- results, conclusion, discussion, methods, abstract, ...
    text     text NOT NULL,
    UNIQUE (paper_id, page, idx)
);
-- Upgrade path for databases created before the section column existed.
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS section text NOT NULL DEFAULT 'front';

CREATE TABLE IF NOT EXISTS claims (
    id           serial PRIMARY KEY,
    paper_id     text NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    text         text NOT NULL,
    conditions   jsonb NOT NULL DEFAULT '{}'::jsonb,
    sentence_ids text[] NOT NULL CHECK (cardinality(sentence_ids) > 0),
    embedding    vector(768)
);
CREATE INDEX IF NOT EXISTS claims_embedding_hnsw ON claims USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS claims_paper_idx ON claims (paper_id);

CREATE TABLE IF NOT EXISTS relationships (
    id                    serial PRIMARY KEY,
    claim_a               int NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    claim_b               int NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    label                 text NOT NULL CHECK (label IN ('supports', 'genuine_contradiction',
                              'apparent_contradiction', 'not_comparable', 'unresolved')),
    similarity            real,
    condition_diff        jsonb NOT NULL DEFAULT '{}'::jsonb,
    explanation           text NOT NULL DEFAULT '',
    key_differences       jsonb NOT NULL DEFAULT '[]'::jsonb,
    cited_sentence_ids    text[] NOT NULL DEFAULT '{}',
    insufficient_evidence boolean NOT NULL DEFAULT false,
    model                 text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (claim_a < claim_b),
    UNIQUE (claim_a, claim_b)
);

CREATE TABLE IF NOT EXISTS impacts (
    id                    serial PRIMARY KEY,
    new_claim_id          int NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    prior_claim_id        int NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    relationship_id       int REFERENCES relationships(id) ON DELETE SET NULL,
    impact                text NOT NULL CHECK (impact IN ('strengthens', 'challenges',
                              'qualifies', 'leaves_unresolved')),
    rationale             text NOT NULL DEFAULT '',
    cited_sentence_ids    text[] NOT NULL DEFAULT '{}',
    insufficient_evidence boolean NOT NULL DEFAULT false,
    model                 text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (new_claim_id, prior_claim_id)
);

-- discovery_cache is shared (public schema): search results keyed by query, not research data.

-- Stable display numbers for graph nodes ("Node N"). Append-only: a number is assigned once, the
-- first time a node appears in the graph, and is never reassigned or reused (new papers/claims get
-- the next number). Cleared only by an explicit reset. Becomes per-session with the session backend.
CREATE TABLE IF NOT EXISTS node_refs (
    node_id text PRIMARY KEY,      -- graph node id, e.g. 'paper:<id>' or 'claim:<id>'
    num     serial UNIQUE
);

-- Research-assistant conversation. `session_id` scopes every read and write so separate research
-- sessions (Stage D) can share this table or move into per-session schemas without code changes.
-- Only verified sentence IDs are stored in `cited_sentence_ids`; quotes are rebuilt from `sentences`.
CREATE TABLE IF NOT EXISTS chat_messages (
    id                    serial PRIMARY KEY,
    session_id            text NOT NULL DEFAULT 'default' CHECK (session_id ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    role                  text NOT NULL CHECK (role IN ('user', 'assistant')),
    content               text NOT NULL,
    context               jsonb,                 -- graph item the question was about, e.g. {"kind":"relationship","id":5}
    cited_sentence_ids    text[] NOT NULL DEFAULT '{}',
    insufficient_evidence boolean NOT NULL DEFAULT false,
    guardrail             text,                  -- why an answer was withheld, if it was
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages (session_id, id);
-- The research question in force when the message was written. After a confirmed question change,
-- earlier messages are shown as belonging to the previous question and are not sent to the model.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS question text;

-- Which papers requested which cached results (many-to-many), so a shared query never
-- re-assigns another paper's discovery results.
CREATE TABLE IF NOT EXISTS discovery_links (
    paper_id     text NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    query_hash   text NOT NULL REFERENCES public.discovery_cache(query_hash) ON DELETE CASCADE,
    requested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (paper_id, query_hash)
);

-- ---------- Stage D5 (additive) ----------
-- Which sentence-extraction method produced a paper's stored sentences. NULL = the original line-based
-- extraction (every paper stored before D5); 2 = block-aware extraction. Stored sentences are never re-extracted.
ALTER TABLE papers ADD COLUMN IF NOT EXISTS ingest_version int;

-- Gemini's own structured answer for newer pair judgments. `model_label` is the label Gemini chose; `label`
-- differs only when code applied a documented guard (see compare.py). NULL for judgments made before D5.
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS comparability text;
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS model_label text;
ALTER TABLE relationships ADD COLUMN IF NOT EXISTS label_note text;

-- Audit trail for relationship/impact classifications. Original labels are never edited: a review records a
-- proposed classification and why, who proposed it (deterministic code, an AI model, or a human), and — only for
-- human decisions — whether it was accepted. Accepted reviews are shown next to the original label; the
-- relationships/impacts rows themselves are not rewritten.
CREATE TABLE IF NOT EXISTS relationship_reviews (
    id              serial PRIMARY KEY,
    target_kind     text NOT NULL CHECK (target_kind IN ('relationship', 'impact')),
    target_id       int NOT NULL,
    original_label  text NOT NULL,
    proposed_label  text,              -- NULL: no relabel proposed (e.g. a note only)
    reason          text NOT NULL,
    reviewer_type   text NOT NULL CHECK (reviewer_type IN ('automated', 'model', 'human')),
    reviewer        text NOT NULL,
    status          text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'accepted', 'rejected')),
    decided_by      text,
    decision_note   text,
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz,
    -- Only a human can accept or reject a proposal.
    CHECK (status = 'proposed' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
-- How a sentence was extracted (ingest_version 2 only; NULL for earlier papers): `block_kind` is body | caption |
-- table | footnote; `text_check` is single_block (inside one PDF text block, text-layer order) or joined_blocks
-- (reconstructed across blocks: verify the wording on the page).
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS block_kind text;
ALTER TABLE sentences ADD COLUMN IF NOT EXISTS text_check text;
CREATE INDEX IF NOT EXISTS relationship_reviews_target ON relationship_reviews (target_kind, target_id);
