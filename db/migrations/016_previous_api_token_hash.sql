-- 016_previous_api_token_hash.sql
-- Keep the prior Bearer token valid after regenerate so open sessions keep working.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS previous_api_token_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS users_previous_api_token_hash_uidx
    ON users (previous_api_token_hash)
    WHERE previous_api_token_hash IS NOT NULL;

COMMENT ON COLUMN users.previous_api_token_hash IS
    'SHA-256 hex of the previous Bearer API token; accepted until the next regenerate.';
