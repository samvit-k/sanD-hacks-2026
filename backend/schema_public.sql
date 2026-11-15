-- ResearchLens SHARED objects (public schema). Research data never lives here: each session's papers,
-- sentences, claims, relationships, impacts, node numbers and chat are in its own schema (schema.sql).
-- Idempotent and non-destructive.
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;

-- Registry of research sessions. schema_name is derived from the id and validated, never user text.
CREATE TABLE IF NOT EXISTS public.sessions (
    id           text PRIMARY KEY CHECK (id ~ '^[a-f0-9]{12}$'),
    schema_name  text NOT NULL UNIQUE CHECK (schema_name = 'rl_' || id),
    title        text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 120),
    -- 'auto': follows the confirmed research question; 'manual': set by the user, never changed automatically
    title_source text NOT NULL DEFAULT 'auto' CHECK (title_source IN ('auto', 'manual')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- One cached result set per (source, normalized query), shared by every paper that asks the same query.
CREATE TABLE IF NOT EXISTS public.discovery_cache (
    query_hash   text PRIMARY KEY,   -- sha256(source + normalized query)
    query        text NOT NULL,
    source       text NOT NULL CHECK (source IN ('brightdata', 'openalex')),
    results      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

