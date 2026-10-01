-- 007_user_api_tokens.sql
-- Opaque API tokens for local auth (sha256 hash only; plaintext shown once at create).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS api_token_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS users_api_token_hash_uidx
    ON users (api_token_hash)
    WHERE api_token_hash IS NOT NULL;

COMMENT ON COLUMN users.api_token_hash IS 'SHA-256 hex of Bearer API token; plaintext never stored.';
