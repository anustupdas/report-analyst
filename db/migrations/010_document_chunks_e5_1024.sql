-- 010_document_chunks_e5_1024.sql
-- multilingual-e5-large is 1024-d (was 1536 for OpenAI text-embedding-3-small).
-- document_chunks was unused until the embedding pipeline landed, so drop+recreate.

DROP INDEX IF EXISTS document_chunks_embedding_hnsw_idx;
DROP TABLE IF EXISTS document_chunks;

CREATE TABLE document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    page_start integer,
    page_end integer,
    token_count integer,
    embedding vector(1024) NOT NULL,
    model_name text NOT NULL DEFAULT 'multilingual-e5-large',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_document_chunk_unique UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS document_chunks_project_id_idx ON document_chunks (project_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_id_idx ON document_chunks (user_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_project_idx
    ON document_chunks (user_id, project_id);

CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE document_chunks IS
    'RAG chunks with pgvector (multilingual-e5-large, 1024-d); filter by user_id + project_id.';
