-- 006_langgraph_checkpoints.sql
-- Matches langgraph-checkpoint-postgres schema so AsyncPostgresSaver.setup()
-- is a no-op when these rows are already recorded in checkpoint_migrations.

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v integer PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    task_path text NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes (thread_id);

-- Mark LangGraph migrations v0–v9 as applied (see checkpoint-postgres MIGRATIONS).
INSERT INTO checkpoint_migrations (v)
SELECT i FROM generate_series(0, 9) AS s (i)
ON CONFLICT (v) DO NOTHING;

COMMENT ON TABLE checkpoints IS 'LangGraph chat/agent state; thread_id = projects.thread_id.';
COMMENT ON TABLE checkpoint_blobs IS 'LangGraph channel blobs.';
COMMENT ON TABLE checkpoint_writes IS 'LangGraph pending writes.';
