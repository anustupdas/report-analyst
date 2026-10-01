-- 009_project_thread_id.sql
-- One LangGraph chat thread per project (owner-scoped). History lives in checkpoints.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS thread_id uuid;

UPDATE projects
SET thread_id = gen_random_uuid()
WHERE thread_id IS NULL;

ALTER TABLE projects
    ALTER COLUMN thread_id SET DEFAULT gen_random_uuid(),
    ALTER COLUMN thread_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS projects_thread_id_uidx
    ON projects (thread_id);

COMMENT ON COLUMN projects.thread_id IS
    'LangGraph checkpoint thread_id for this project''s chat (unique per project).';
