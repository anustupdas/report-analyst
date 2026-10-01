-- LangGraph's AsyncPostgresSaver.setup() replays any checkpoint_migrations
-- version it has not recorded. Migration 006 created checkpoint_writes with
-- task_path already present and, on databases migrated before v9 was recorded,
-- left checkpoint_migrations at v8. setup() then runs:
--   ALTER TABLE checkpoint_writes ADD COLUMN task_path ...
-- which fails because the column exists, and the server falls back to memory.
--
-- v9 in langgraph-checkpoint-postgres is exactly that ADD COLUMN.

ALTER TABLE checkpoint_writes
    ADD COLUMN IF NOT EXISTS task_path text NOT NULL DEFAULT '';

INSERT INTO checkpoint_migrations (v)
VALUES (9)
ON CONFLICT (v) DO NOTHING;
