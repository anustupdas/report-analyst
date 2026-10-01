-- Key datapoints (FTE + sustainability goals) from the shared supervisor datapoints job.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS key_datapoints JSONB;

COMMENT ON COLUMN documents.key_datapoints IS
    'Structured FTE + sustainability_goals JSON from supervisor datapoints mode; null until extracted.';
