# Annual Report Analyst

An AI assistant for analysing annual reports. Upload annual-report PDFs
(e.g. ABN AMRO, ASML, Shell, Heineken), and ask questions such as
*“How much did Shell spend on climate change adaptation in 2025?”*. Answers
quote the report verbatim with page citations, and key datapoints (FTE count,
sustainability goals) are extracted at ingest.

Author: [Anustup Das](https://github.com/anustupdas).

Local prototype for the Aannual-report RAG case assignment. The repo
folder, Python package (`chat_api`) and Postgres database (`chat_project`)
keep their original names.

## Start with Docker

Docker is enough. One `.env` file is passed to every container. You need Docker
with Compose, and an OpenAI key for chat.

1. Copy the env file and fill the OpenAI key.

```bash
cp .env.example .env
```

Put `OPENAI_API_KEY` in `.env`. Leave the other lines as they are unless you
want a different choice below.

2. Build the images.

```bash
docker compose --env-file .env build
```

3. Start the stack.

```bash
docker compose --env-file .env up
```

Open http://localhost:8000/. Chat from the browser uses http://localhost:8080.
Stop with Ctrl-C, or `docker compose --env-file .env down` if you started with
`-d`. That keeps the database volume and the uploaded files.

`make docker-up` is the same as step 2 and step 3 together (`up --build`).
`make docker-down` stops the containers.

Optional lines in the same `.env`:

| Key | Default | What it does |
|---|---|---|
| `DATA_DIR` | `./data` | Host folder mounted at `/data` for uploads. Use an absolute path if the reports already live somewhere else. |
| `TEXT_EXTRACTION_USE_OCR` | `false` | `false` is PyMuPDF. `true` is Mistral OCR and needs `MISTRAL_API_KEY`. |
| `EMBEDDING_PROVIDER` | `openai` | `openai` (`text-embedding-3-small`, 1536) or `huggingface` (`gte-multilingual-base`, 768). The API sets the matching model and size at start. Hugging Face loads the local model on startup. |

Chat still needs `OPENAI_API_KEY` when embeddings are Hugging Face. Changing
`EMBEDDING_PROVIDER` does not rewrite vectors already stored; process those
reports again. After you edit `.env`, start again with
`docker compose --env-file .env up --build`.

For local development without Docker, the same repo still runs as processes:

```bash
cp .env.example .env
make setup
make run
make status
```

| Command | What it does |
|---|---|
| `make setup` | Install all app deps, start Postgres, migrate |
| `make run` | Postgres + text-extraction (:5000) + embedding (:5100) + API (:8000) + LangGraph (:8080) |
| `make stop` | Stop API, LangGraph, embedding, and extraction (Postgres stays up) |
| `make db-down` | Stop Postgres (keeps data) |
| `make lint` | flake8 + ruff (api + extraction + embedding + langgraph) |
| `make format` | black (api + extraction + embedding + langgraph) |
| `make test` | pytest for all four Python apps |
| `make langfuse-upload` | Push local fallback prompts to Langfuse |

## Configuration

`.env` files hold **secrets only**. Every other setting lives in the service's
own `configs/staging/config.yml` (a `production/` copy sits next to it).

| Service | Settings (YAML) | Secrets (`.env`) |
|---|---|---|
| repo root | — (`docker-compose.yml` defaults) | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `OPENAI_API_KEY`, `AGENT_SECRET`, `MISTRAL_API_KEY`; Docker also reads `DATA_DIR`, `TEXT_EXTRACTION_USE_OCR`, `EMBEDDING_PROVIDER` |
| `apps/api` | ports, DB host/port/name, data root, service URLs, timeouts, embedding model + dimension, chunk sizes, search limits, upload limits, CORS, Langfuse tracing | `AGENT_SECRET`, `INTERNAL_WORKFLOW_SECRET`, `OPENAI_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` (optional `DATABASE_URL` override) |
| `apps/langgraph-server` | port, API URL, checkpoint backend, CORS, search default limit, Langfuse tracing | `AGENT_SECRET`, `OPENAI_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` (reads `apps/api/.env` too) |
| `apps/text-extraction-service` | port, data dir, `storage.useS3`, `mistral.enabled`, OCR model, S3 bucket/region | `MISTRAL_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| `apps/embedding-service` | port, enabled models, prefetch, batch size | optional `HF_TOKEN` |

For `make run`, non-secret keys found in a `.env` are ignored with a warning.
Real process environment variables still override YAML. Docker is the
exception: Compose injects the root `.env` as process environment, so
`TEXT_EXTRACTION_USE_OCR` and `EMBEDDING_PROVIDER` apply when the containers
start. The Makefile reads ports from the YAML files. Point a service at
another config with `API_CONFIG_PATH` (API) or `REMOTE_CONFIG_URL`
(extraction/embedding).

- API docs: http://localhost:8000/docs
- UI: http://localhost:8000/
- LangGraph: http://localhost:8080/docs
- Extraction: http://localhost:5000/docs
- Embedding: http://localhost:5100/docs

Stop everything at the end of a session:

```bash
make stop
make db-down
```

## Web UI

Zero-build SPA served by the API (same origin as `/api/v1`).

- **Sign-in and home** `/` — create account, paste token, or regenerate token; after sign-in, report projects
- **Workspace** `/project/{id}` — reports | report canvas | report analyst
- Sign-in and the signed-in home warn not to upload personal documents: file text can be sent to an external LLM, and no GDPR guardrail is in place yet
- Dark mode toggle on sign-in, home, and the workspace. The choice is stored as `report_rag_theme`; with nothing stored, the page follows the system theme
- Each project has a unique `threadId` for the later LangGraph checkpoint
- Upload from the reports `+`, the canvas **Upload annual report** card, or the analyst `+`
- Report canvas shows company, report year, document type, and FTE / sustainability key datapoints
- Analyst chat streams from LangGraph (`/supervisor/stream`) with page citations
- Refresh re-runs the shared supervisor in datapoints mode (`ready` while indexing, else full index)
- Each pane scrolls on its own (page does not grow with the thread)

See [`apps/web/README.md`](apps/web/README.md). Architecture and how the five
processes connect: [`docs/system-overview.md`](docs/system-overview.md).
Per-service options (OCR, embeddings, Langfuse): [`docs/service-options.md`](docs/service-options.md).

## Ingest flow (API)

```bash
# 1) create user (save apiToken — shown once)
curl -s http://localhost:8000/api/v1/users -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","displayName":"You"}'

# 2) create project / tab (response includes threadId)
curl -s http://localhost:8000/api/v1/projects -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"Shell 2024"}'

# 3) prepare → upload → process
curl -s http://localhost:8000/api/v1/projects/$PROJECT/documents/prepare \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"filename":"shell.pdf","title":"Shell AR 2024"}'

curl -s -X PUT "http://localhost:8000/api/v1/projects/$PROJECT/documents/$DOC/content" \
  -H "Authorization: Bearer $TOKEN" -F "file=@./shell.pdf"

curl -s -X POST "http://localhost:8000/api/v1/projects/$PROJECT/documents/$DOC/process" \
  -H "Authorization: Bearer $TOKEN"
```

Status machine: `pending → processing → extracted → ingesting → ready → completed` (or `failed`).

`process` extracts, then chunks, embeds with the configured provider (local default:
OpenAI `text-embedding-3-small`, 1536-d), and upserts that provider's pgvector table.
Search a project with:

```bash
curl -s http://localhost:8000/api/v1/projects/$PROJECT/chunks/search \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"What was net profit in 2024?","limit":8}'
```

If you lose the token after logout:

```bash
curl -s http://localhost:8000/api/v1/users/regenerate-token \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com"}'
```

## Auth planes

| Plane | Header |
|---|---|
| User | `Authorization: Bearer <apiToken>` |
| Agent (LangGraph → API) | `X-Agent-Secret` |
| Internal workers | `X-Internal-Secret` |

## Shared code

Each app is its own process and its own Python package. Copied glue between
them is fine for this repo: prompt loading, the Langfuse client, YAML config
loading, and the small service CLIs. Those copies differ only by import path
and by which settings each app owns.

A business rule that writes data must exist once. Merging FTE and
sustainability goals lives only in `chat_api.modules.vectors.merge_key_datapoints`.
LangGraph returns a fresh extract; the API merges it onto the document.

The later home for the glue is a separate foundation package, published from
its own repo and installed by each app. It would hold prompt loading, Langfuse
setup, and YAML config loading. Datapoint merge stays in the API, because that
is the service that owns `documents.key_datapoints`.

## Repo layout

```
apps/
  api/                      # orchestration + static UI + pgvector CRUD/search
  langgraph-server/         # ReAct supervisor + SSE chat + datapoints extract + checkpoints
  text-extraction-service/  # PDF/OCR extraction
  embedding-service/        # HF gte (768-d) and OpenAI embeddings (1536-d); GET /metrics
  web/                      # zero-build SPA
data/                       # local file store
db/migrations/              # SQL schema (includes projects.thread_id)
```

Ingest fills company name, report year and a short description with an LLM
(`gpt-6-luna`, prompt in `apps/api/src/chat_api/prompt/`).
Key datapoints (FTE + sustainability goals) are filled by the same LangGraph
supervisor in `mode=datapoints` at `ready` and again at `completed` (UI Refresh
re-runs the job). Document-type classification is still a deliberate placeholder.
