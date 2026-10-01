-- 013_artifact_extracted_layout.sql
-- Ingest now stores Mistral/PyMuPDF layout JSON as identifier 'extracted-layout'.
-- The check from 008 did not include that value, so process failed after extract.

ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_identifier_check;

ALTER TABLE artifacts
    ADD CONSTRAINT artifacts_identifier_check CHECK (
        identifier IN (
            'original-pdf',
            'original',
            'extracted-text',
            'extracted-layout',
            'summary'
        )
    );
