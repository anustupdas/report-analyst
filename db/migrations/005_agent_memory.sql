-- 005_agent_memory.sql
-- Durable preferences / facts (optional). Chat history lives in LangGraph checkpoints.

CREATE TABLE IF NOT EXISTS agent_memory (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id uuid REFERENCES projects (id) ON DELETE CASCADE,
    kind text NOT NULL,
    value jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_memory_kind_check CHECK (
        kind IN ('preference', 'fact', 'note')
    )
);

CREATE INDEX IF NOT EXISTS agent_memory_user_id_idx ON agent_memory (user_id);
CREATE INDEX IF NOT EXISTS agent_memory_project_id_idx ON agent_memory (project_id);
CREATE INDEX IF NOT EXISTS agent_memory_user_project_idx
    ON agent_memory (user_id, project_id);

COMMENT ON TABLE agent_memory IS 'Cross-turn agent memory; not a chat message store.';
