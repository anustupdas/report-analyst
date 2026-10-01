-- 015_document_ingest_statuses.sql
-- Streaming ingest: extraction finished, then embedding in progress, then searchable
-- while the remaining batches are still being stored.
--   processing → extracted → ingesting → ready → completed

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check;

ALTER TABLE documents
    ADD CONSTRAINT documents_status_check CHECK (
        status IN (
            'pending',
            'processing',
            'extracted',
            'ingesting',
            'ready',
            'completed',
            'failed'
        )
    );
