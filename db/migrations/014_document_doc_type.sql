-- 014_document_doc_type.sql
-- Classification result of the ingest pipeline: is the upload an annual report?
-- NULL until the classifier (placeholder for now) is connected.

ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type text;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_doc_type_check;
ALTER TABLE documents
    ADD CONSTRAINT documents_doc_type_check CHECK (doc_type IS NULL OR doc_type IN ('annual_report', 'other'));

COMMENT ON COLUMN documents.doc_type IS 'annual_report | other; NULL = not classified yet';
