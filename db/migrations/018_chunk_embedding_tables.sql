-- Split vector storage from chunk text.
-- document_chunks keeps content/metadata; provider-specific vectors live in:
--   document_chunk_embeddings_hf      vector(768)  — local Hugging Face (gte)
--   document_chunk_embeddings_openai  vector(1536) — OpenAI text-embedding-3-small
--
-- Existing HF vectors are copied, then document_chunks.embedding is dropped.

CREATE TABLE IF NOT EXISTS document_chunk_embeddings_hf (
    chunk_id uuid PRIMARY KEY REFERENCES document_chunks (id) ON DELETE CASCADE,
    embedding vector(768) NOT NULL,
    model_name text NOT NULL DEFAULT 'gte-multilingual-base',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunk_embeddings_openai (
    chunk_id uuid PRIMARY KEY REFERENCES document_chunks (id) ON DELETE CASCADE,
    embedding vector(1536) NOT NULL,
    model_name text NOT NULL DEFAULT 'text-embedding-3-small',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Backfill HF table from the legacy column when present.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'document_chunks'::regclass
          AND attname = 'embedding'
          AND NOT attisdropped
    ) THEN
        INSERT INTO document_chunk_embeddings_hf (chunk_id, embedding, model_name)
        SELECT id, embedding, COALESCE(NULLIF(model_name, ''), 'gte-multilingual-base')
        FROM document_chunks
        ON CONFLICT (chunk_id) DO UPDATE
        SET embedding = EXCLUDED.embedding,
            model_name = EXCLUDED.model_name,
            updated_at = now();
    END IF;
END $$;

DROP INDEX IF EXISTS document_chunks_embedding_hnsw_idx;

ALTER TABLE document_chunks
    DROP COLUMN IF EXISTS embedding;

CREATE INDEX IF NOT EXISTS document_chunk_embeddings_hf_hnsw_idx
    ON document_chunk_embeddings_hf
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS document_chunk_embeddings_openai_hnsw_idx
    ON document_chunk_embeddings_openai
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS document_chunk_embeddings_hf_model_idx
    ON document_chunk_embeddings_hf (model_name);

CREATE INDEX IF NOT EXISTS document_chunk_embeddings_openai_model_idx
    ON document_chunk_embeddings_openai (model_name);

COMMENT ON TABLE document_chunks IS
    'RAG chunk text + metadata (no vector). Vectors live in document_chunk_embeddings_*.';
COMMENT ON TABLE document_chunk_embeddings_hf IS
    'Hugging Face / gte-multilingual-base vectors (768-d), keyed by document_chunks.id.';
COMMENT ON TABLE document_chunk_embeddings_openai IS
    'OpenAI text-embedding-3-small vectors (1536-d), keyed by document_chunks.id.';
