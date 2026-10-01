-- 008_artifact_original_identifier.sql
-- Allow a generic 'original' artifact for non-PDF uploads (PDF keeps 'original-pdf').

ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_identifier_check;

ALTER TABLE artifacts
    ADD CONSTRAINT artifacts_identifier_check CHECK (
        identifier IN ('original-pdf', 'original', 'extracted-text', 'summary')
    );
