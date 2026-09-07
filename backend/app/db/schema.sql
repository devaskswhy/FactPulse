-- FactPulse schema
--
-- Design note: fact_type is deliberately free text (no enum, no lookup-table
-- foreign key) and per-fact detail lives in the EAV table fact_attributes.
-- This lets the extractor invent new kinds of facts at runtime without a
-- migration. See docs/ARCHITECTURE.md.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- documents

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT    NOT NULL,
    title       TEXT,
    sha256      TEXT    NOT NULL UNIQUE,
    uploaded_at TEXT    NOT NULL DEFAULT (datetime('now')),
    page_count  INTEGER,
    status      TEXT    NOT NULL DEFAULT 'uploaded'
);

CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

-- ------------------------------------------------------------------ chunks

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_start  INTEGER,
    page_end    INTEGER,
    text        TEXT    NOT NULL,
    token_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);

-- ------------------------------------------------------------------- facts
--
-- fact_type is free text on purpose. normalized_value/unit/time_scope are the
-- three axes we reconcile on most often, so they are promoted to columns;
-- everything else about a fact goes in fact_attributes.
--
-- quote + page_number + bbox_* are the grounding: every fact must point at the
-- span of source PDF it came from.

CREATE TABLE IF NOT EXISTS facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id      INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id         INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
    fact_type        TEXT    NOT NULL,
    subject          TEXT,
    statement        TEXT    NOT NULL,
    normalized_value TEXT,
    unit             TEXT,
    time_scope       TEXT,
    quote            TEXT,
    page_number      INTEGER,
    bbox_x0          REAL,
    bbox_y0          REAL,
    bbox_x1          REAL,
    bbox_y1          REAL,
    confidence       REAL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_facts_document_id ON facts(document_id);
CREATE INDEX IF NOT EXISTS idx_facts_chunk_id    ON facts(chunk_id);
CREATE INDEX IF NOT EXISTS idx_facts_fact_type   ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_subject     ON facts(subject);

-- -------------------------------------------------------- fact_attributes
--
-- EAV sidecar. Anything the extractor wants to record that has no column:
-- ('segment', 'EMEA'), ('basis', 'non-GAAP'), ('restated', 'true'), ...

CREATE TABLE IF NOT EXISTS fact_attributes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    key     TEXT    NOT NULL,
    value   TEXT
);

CREATE INDEX IF NOT EXISTS idx_fact_attributes_fact_id ON fact_attributes(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_attributes_key     ON fact_attributes(key);
CREATE INDEX IF NOT EXISTS idx_fact_attributes_kv      ON fact_attributes(key, value);

-- -------------------------------------------------------------- fact_types
--
-- A registry, not a constraint. Rows appear here as a side effect of facts
-- being written, so the UI can list "what kinds of facts exist in this corpus"
-- without a SELECT DISTINCT over the whole facts table.

CREATE TABLE IF NOT EXISTS fact_types (
    name            TEXT PRIMARY KEY,
    first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    example_fact_id INTEGER REFERENCES facts(id) ON DELETE SET NULL,
    fact_count      INTEGER NOT NULL DEFAULT 0
);

-- -------------------------------------------------------------- embeddings
--
-- vector is a raw float32 BLOB (numpy .tobytes()); dim lets us read it back
-- and lets embedding models change without a migration.

CREATE TABLE IF NOT EXISTS embeddings (
    fact_id INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    vector  BLOB    NOT NULL,
    dim     INTEGER NOT NULL
);

-- ----------------------------------------------------------- relationships
--
-- The cross-document layer: corroborates / contradicts / reconcilable / etc.
-- relationship_type is free text for the same reason fact_type is.

CREATE TABLE IF NOT EXISTS relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id_a         INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    fact_id_b         INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    relationship_type TEXT    NOT NULL,
    rationale         TEXT,
    confidence        REAL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_relationships_fact_a ON relationships(fact_id_a);
CREATE INDEX IF NOT EXISTS idx_relationships_fact_b ON relationships(fact_id_b);
CREATE INDEX IF NOT EXISTS idx_relationships_type   ON relationships(relationship_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_pair
    ON relationships(fact_id_a, fact_id_b, relationship_type);

-- ------------------------------------------------------------ review_queue
--
-- Anything the pipeline was not confident about: an ungrounded quote, a
-- low-confidence extraction, a contradiction a human should adjudicate.

CREATE TABLE IF NOT EXISTS review_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id    INTEGER REFERENCES facts(id) ON DELETE CASCADE,
    chunk_id   INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
    issue_type TEXT    NOT NULL,
    note       TEXT,
    resolved   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_queue_resolved ON review_queue(resolved);
CREATE INDEX IF NOT EXISTS idx_review_queue_fact_id  ON review_queue(fact_id);
