# Database

Author: [Anustup Das](https://github.com/anustupdas).

Postgres 16 + pgvector, started via Docker Compose at the repo root. The full
stack, including this database, is the three Docker steps in the repo README.
`docker compose --env-file .env up` runs migrations after Postgres is healthy.

From repo root:

```bash
make setup     # install all apps + DB + migrations
make run       # start Postgres + app services
make db-seed   # optional demo user/project
make db-down   # stop Postgres (keep volume)
```

Migrations are plain SQL under `migrations/`, applied once and tracked in
`schema_migrations`. LangGraph checkpoint tables are created in
`006_langgraph_checkpoints.sql` to match langgraph-checkpoint-postgres.

`009_project_thread_id.sql` adds `projects.thread_id` (unique UUID). The UI
and API use it as the LangGraph checkpoint thread for that project’s chat.
There is no `chat_messages` table. The transcript lives in the LangGraph
checkpoint tables.

`010_document_chunks_e5_1024.sql` and `011_document_chunks_gte_768.sql` are
historical. They put a vector column on `document_chunks` (first 1024-d, then
768-d) and marked older `completed` documents `failed` so they would be
re-processed. `018` removes that column. Live vectors are in the two tables
below. The API refuses to start if `embedding.dimension` does not match the
active provider (768 for Hugging Face, 1536 for OpenAI).

`012_document_chunks_section.sql` adds `document_chunks.section` (the chunk's
section path) for structure-aware chunking, clears the old 800-char chunks and
marks `completed` documents `failed` so they get re-processed.

`013_artifact_extracted_layout.sql` allows the `extracted-layout` artifact.
`014_document_doc_type.sql` adds `documents.doc_type` (`annual_report` | `other`,
NULL until the classifier is connected).

`018_chunk_embedding_tables.sql` moves vectors off `document_chunks` into
`document_chunk_embeddings_hf` (768-d) and `document_chunk_embeddings_openai`
(1536-d). Chunk text stays in `document_chunks`; `embedding.provider` in the
API config selects which table ingest/search use. Startup refuses a dimension
that does not match that provider.

`019_checkpoint_writes_task_path.sql` records LangGraph checkpoint migration
v9 when an older database already has `checkpoint_writes.task_path` but not
the version row. A fresh migrate inserts v0–v9 in `006`, so this file is a
no-op there.

Tables:

- users (api token hash)
- projects (`thread_id`)
- documents, artifacts, document_chunks
- document_chunk_embeddings_hf, document_chunk_embeddings_openai
- agent_memory
- checkpoints, checkpoint_blobs, checkpoint_writes

Not included (by design):

- `chat_threads` / `chat_messages` → LangGraph checkpoints
- Separate `extracted_datapoints` table → JSONB `documents.key_datapoints` (migration `017`)
