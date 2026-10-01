# Annual Report Analyst API

Author: [Anustup Das](https://github.com/anustupdas).

Python FastAPI backend for the Annual Report Analyst: users, report projects,
annual-report ingest (extract → chunk → embed → pgvector) and vector search.
The Python package is still named `chat_api` (`pip` name `chat-api`).
Also serves the zero-build UI from `apps/web` at `/` and `/project/{id}`.
The sign-in page and the signed-in home warn that uploaded text can be sent
to an external LLM and that no GDPR guardrail is in place yet.

## Layers

```
http/v1/          thin routes (controllers)
modules/*/        actions, queries, policies, models, enums
services/         outbound clients (extraction, embedding, document description, classifier*)
prompt/           LLM prompts + model config (Langfuse-compatible fallbacks)
workflows/        multi-step ingest orchestration
```

`*` = placeholder until that service exists.

The API is the only writer of documents and chunks. LangGraph returns a fresh
datapoints extract; `merge_key_datapoints` in this package is what stores it.
Design of that split: [system overview](../../docs/system-overview.md#design).

Ingest, in order:

1. Extract the file (PyMuPDF by default, Mistral when `textExtraction.useOcr` is true).
2. Classify the document type: placeholder (`services/placeholders.py`) until the JEV model is
   connected; `doc_type` stays empty.
3. Chunk the per-page text (see below).
4. Describe the document on a side thread: the first `llm.descriptionContextChunks` chunks
   (default 10, which usually covers the cover, contents and introduction) go to OpenAI with
   structured output (`DocumentDescription`: `company_name`, `report_year`, `description`).
   The results fill `documents.company_name`, `report_year` and `summary`, plus a `summary`
   artifact. This step is non-fatal: if it is disabled, has no key, or fails, it is logged
   and ingest continues. It does not change `status`.
5. Embed via embedding-service, in batches of 32, overlapping the description.
   `embedding.provider=openai` calls `POST /api/v1/embeddings/openai` and upserts
   `document_chunk_embeddings_openai` (1536-d). `huggingface` uses the passage lane and
   `document_chunk_embeddings_hf` (768-d). The first stored batch sets `ready` and starts
   a datapoints pass; the last batch sets `completed` and runs that pass again.

Prompts (`src/chat_api/prompt/`) follow the Langfuse layout: `load_prompt(agent, node, name)`
resolves Langfuse (`ingest/describe_document/main`, labels `dev` then `production`) then the
local fallback in `fallback_prompts/{agent}/{node}/{name}.py`. It returns the chat messages
(`{{variable}}` placeholders) and `config.model_config`. `make langfuse-upload` pushes local
fallbacks to Langfuse. Document description calls are traced when `langfuse.tracing` is on.

Chunking (`services/chunking.py`, settings under `chunking.*` in the YAML config):

- Works for both extractors: PyMuPDF plain text and Mistral OCR markdown (`#` headings, `|` tables).
- Strips running page headers/footers (company name, report title, page numbers). For PyMuPDF the
  running header becomes the chunk's section path, e.g. `Risk, funding & capital › Risk management`;
  for markdown the heading hierarchy does.
- Rejoins hard-wrapped lines and sentences split by a page break.
- Splits at section, then paragraph, then sentence, then word boundaries. Target ~2000 chars, never more
  than 2500 (~550–700 gte tokens). Chunks in the same section share their last 2 whole sentences;
  large tables repeat their header row.
- Each chunk stores `section`, `page_start`/`page_end` and a word count. The section path is prepended
  to the text that gets embedded; `content` itself stays clean for citations.

Search: `POST /api/v1/projects/{id}/chunks/search` with `{ "query": "..." }` embeds the question with the active provider, then cosine-searches that provider's table. Hugging Face sends `input_type=query`. OpenAI sends the text with no prefix. Agent plane: `POST /api/v1/agent/projects/{id}/chunks/search`. Only `ready` and `completed` documents are searchable.

## Configuration

Non-secret settings live in `configs/staging/config.yml` (`configs/production/`
for prod; choose with `API_CONFIG_PATH`). Relative paths resolve against
`apps/api/`. Secrets come from `.env` files: the repo-root `.env`
(`POSTGRES_USER`, `POSTGRES_PASSWORD`), then `apps/api/.env` (`AGENT_SECRET`,
`INTERNAL_WORKFLOW_SECRET`, `OPENAI_API_KEY`, optional full `DATABASE_URL`). The DB URL is built
from `database.*` in YAML plus those credentials. Non-secret keys in `.env` are
ignored with a warning; process env vars still override YAML.

Docker uses the root `.env` only. See the three steps in the repo README.
`TEXT_EXTRACTION_USE_OCR` and `EMBEDDING_PROVIDER` in that file are injected
as process environment, so they override the YAML when the API container
starts. The image entrypoint then sets the embedding model and dimension to
match the provider.

## Auth planes

| Caller | Mechanism |
|--------|-----------|
| User / UI | `Authorization: Bearer <api_token>` (issued once at `POST /api/v1/users`) |
| Agent (LangGraph) | `X-Agent-Secret` (env `AGENT_SECRET`) |
| Internal workers | `X-Internal-Secret` (env `INTERNAL_WORKFLOW_SECRET`) — reserved |

Tokens are stored as SHA-256 hashes only. `POST /api/v1/users/regenerate-token` issues a new one (local recovery after logout). The previous token stays valid until the next regenerate so open sessions keep working.

## Main flow

1. `POST /api/v1/users` → save `apiToken`
2. `POST /api/v1/projects` → create tab + unique `threadId`
3. `POST /api/v1/projects/{id}/documents/prepare` → pending document + folder
4. `PUT  /api/v1/projects/{id}/documents/{docId}/content` → upload bytes
5. `POST /api/v1/projects/{id}/documents/{docId}/process` → atomic claim + ingest workflow
6. Poll `GET .../documents/{docId}` or `GET /api/v1/me/projects/state`
7. `GET .../documents/{docId}/original` → inline PDF preview (Bearer auth)

Status: `pending → processing → extracted → ingesting → ready → completed` (or `failed`).

Ingest writes, per document folder `data/{userId}/{projectId}/{documentId}/`:

- `extracted-text.txt`: the text that is chunked and embedded.
- `extracted-layout.json`: method, model, usage and the per-page layout (markdown,
  header/footer, dimensions, tables, blocks with bounding boxes, confidence). It is
  enough to re-render the document in a UI.
- `images/p{page}-{id}`: the page images Mistral returned (referenced from the JSON).

With Mistral, the page header becomes the chunk's section hint, and the footer is dropped.
Set `textExtraction.useOcr: true` to ingest through Mistral.

## Run

From monorepo root:

```bash
make setup
make run
make test
make lint
```

API: http://localhost:8000/docs  
UI: http://localhost:8000/
