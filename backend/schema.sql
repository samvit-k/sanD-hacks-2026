-- ResearchLens schema (Tiger Data: PostgreSQL + pgvector).
-- Idempotent and non-destructive: safe to re-run on an initialized database (never drops data).
CREATE EXTENSION IF NOT EXISTS vector;

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
ALTER TABLE papers ADD COLUMN IF NOT EXISTS analysis_status text NOT NULL DEFAULT 'complete'
    CHECK (analysis_status IN ('processing', 'complete'));
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

-- One cached result set per (source, normalized query), shared by every paper that asks the same query.
CREATE TABLE IF NOT EXISTS discovery_cache (
    query_hash   text PRIMARY KEY,   -- sha256(source + normalized query)
    query        text NOT NULL,
    source       text NOT NULL CHECK (source IN ('brightdata', 'openalex')),
    results      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

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

-- Which papers requested which cached results (many-to-many), so a shared query never
-- re-assigns another paper's discovery results.
CREATE TABLE IF NOT EXISTS discovery_links (
    paper_id     text NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    query_hash   text NOT NULL REFERENCES discovery_cache(query_hash) ON DELETE CASCADE,
    requested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (paper_id, query_hash)
);
