-- 003_documents_artifacts.sql
-- Uploaded PDFs + local filesystem pointers (S3 stand-in).

CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title text NOT NULL,
    original_filename text NOT NULL,
    mime_type text NOT NULL DEFAULT 'application/pdf',
    status text NOT NULL DEFAULT 'pending',
    company_name text,
    report_year integer,
    summary text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT documents_status_check CHECK (
        status IN ('pending', 'processing', 'ready', 'completed', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS documents_project_id_idx ON documents (project_id);
CREATE INDEX IF NOT EXISTS documents_user_id_idx ON documents (user_id);
CREATE INDEX IF NOT EXISTS documents_status_idx ON documents (status);
CREATE INDEX IF NOT EXISTS documents_project_status_idx ON documents (project_id, status);

CREATE TABLE IF NOT EXISTS artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    identifier text NOT NULL,
    storage_path text NOT NULL,
    mime_type text,
    size_bytes bigint,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifacts_identifier_check CHECK (
        identifier IN ('original-pdf', 'extracted-text', 'summary')
    ),
    CONSTRAINT artifacts_document_identifier_unique UNIQUE (document_id, identifier)
);

CREATE INDEX IF NOT EXISTS artifacts_document_id_idx ON artifacts (document_id);

COMMENT ON TABLE documents IS 'One uploaded annual-report PDF (Studocu Source equivalent).';
COMMENT ON COLUMN documents.status IS 'pending → processing → ready → completed | failed';
COMMENT ON TABLE artifacts IS 'Local disk paths for PDF / extracted text / summary under data/{user}/{project}/{doc}/.';
