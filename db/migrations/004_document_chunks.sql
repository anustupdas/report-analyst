-- 004_document_chunks.sql
-- Chunked report text + pgvector embeddings (Milvus stand-in).
-- Default dim 1536 matches OpenAI text-embedding-3-small.
-- If you switch embedding models, recreate this table with the matching dim.

CREATE TABLE IF NOT EXISTS document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    page_start integer,
    page_end integer,
    token_count integer,
    embedding vector(1536) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT document_chunks_document_chunk_unique UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_document_id_idx ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS document_chunks_project_id_idx ON document_chunks (project_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_id_idx ON document_chunks (user_id);
CREATE INDEX IF NOT EXISTS document_chunks_user_project_idx
    ON document_chunks (user_id, project_id);

-- IVFFlat needs data to build usefully; HNSW works better from empty.
-- Lists/probes can be tuned later for larger corpora.
CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw_idx
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE document_chunks IS 'RAG chunks with pgvector; filter by user_id + project_id (+ document_id).';
