-- 012_document_chunks_section.sql
-- Structure-aware chunking: each chunk records its section path (e.g.
-- "Risk, funding & capital › Risk management") and its page range.
-- Existing chunks used the old 800-char splitter without pages, so they are
-- dropped and their documents must be re-processed.

ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section text;

COMMENT ON COLUMN document_chunks.section IS
    'Section path the chunk belongs to; prepended to the text when embedding.';

DELETE FROM document_chunks;

UPDATE documents
SET status = 'failed',
    error_message = 'Chunking strategy changed; re-process to re-index.',
    updated_at = now()
WHERE status = 'completed';
