-- 011_document_chunks_gte_768.sql
-- Switch embeddings from intfloat/multilingual-e5-large (1024-d) to
-- Alibaba-NLP/gte-multilingual-base (768-d).
-- Vectors from different models are not comparable, so existing chunks are
-- dropped; re-process documents to re-embed them.

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
    -- vector(768) makes Postgres reject any vector with a different dimension.
    embedding vector(768) NOT NULL,
    model_name text NOT NULL DEFAULT 'gte-multilingual-base',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_document_chunk_unique UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS document_chunks_project_id_idx ON document_chunks (project_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_id_idx ON document_chunks (user_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_project_model_idx
    ON document_chunks (user_id, project_id, model_name);

CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE document_chunks IS
    'RAG chunks with pgvector (gte-multilingual-base, 768-d); filter by user_id + project_id + model_name.';

-- Documents whose chunks were dropped above must be re-processed.
UPDATE documents
SET status = 'failed',
    error_message = 'Embedding model changed to gte-multilingual-base; re-process to re-index.',
    updated_at = now()
WHERE status = 'completed';
