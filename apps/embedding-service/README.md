# Embedding Service

Author: [Anustup Das](https://github.com/anustupdas).

FastAPI microservice with two encode paths and no database. The API chooses
the path with `embedding.provider` and stores the vectors itself. This process
only returns floats.

| Provider | Route | Model | Size |
|---|---|---|---|
| `openai` (default) | `POST /api/v1/embeddings/openai` | `text-embedding-3-small` | 1536 |
| `huggingface` | `POST /api/v1/embed_text` and `/embed_texts` | `gte-multilingual-base` | 768 |

OpenAI needs `OPENAI_API_KEY` and does not load a local model. Hugging Face
loads [`Alibaba-NLP/gte-multilingual-base`](https://huggingface.co/Alibaba-NLP/gte-multilingual-base)
(768-d, up to 8192 tokens, 70+ languages) from the Hub into
`~/.cache/huggingface`. Weights are not stored in this repo. There is no
MLflow or S3 weight store.

How this sits in the system: [system overview](../../docs/system-overview.md#design).
The Docker switch is `EMBEDDING_PROVIDER` in the root `.env`
([service options](../../docs/service-options.md)).

## Layout

```
apps/embedding-service/
  configs/staging|production/config.yml
  script/request.py
  src/embedding_service/
    api_v1/router.py
    app.py
    constants.py
    exceptions.py
    metrics.py
    run.py
    service.py
    utils.py
    version.py
  tests/embedding_service/
```

## Config

`configs/staging/config.yml` is the source of truth for the model id:

```yaml
models.enabled:
  - gte-multilingual-base
models.prefetchOnStart: true
models.warmupOnStart: true
gte-multilingual-base.hfModelId: Alibaba-NLP/gte-multilingual-base
gte-multilingual-base.dimension: 768
gte-multilingual-base.maxSeqLength: 8192
gte-multilingual-base.trustRemoteCode: true
gte-multilingual-base.extraHubRepos:
  - Alibaba-NLP/new-impl
gte-multilingual-base.queryPrefix: ""
gte-multilingual-base.passagePrefix: ""
```

When prefetch is on, startup runs `snapshot_download` for the model and any
`extraHubRepos` so files sit in the HF cache. `SentenceTransformer(...)` loads
on the first Hugging Face request, or at startup when `models.warmupOnStart`
is true. The OpenAI route never loads it. Docker turns prefetch and warmup
off unless `EMBEDDING_PROVIDER=huggingface`.

gte's modeling code lives in `Alibaba-NLP/new-impl`, so it loads with
`trust_remote_code=True` (code from that Hub repo runs locally).

`input_type` is `passage` for document chunks and `query` for search questions.
Each model's `queryPrefix` / `passagePrefix` is prepended (gte uses none; E5
would use `query: ` / `passage: `).

## Encode pool (query vs passage)

The service runs **two dedicated worker threads**:

| Lane | Handles | Behavior |
|---|---|---|
| query | `input_type=query` | Always reserved for search embeds |
| passage | `input_type=passage` | Dedicated to ingest batches |

Each lane uses its own in-memory model copy once first used (or at startup when
`models.warmupOnStart` is true), so a long ingest batch cannot block a search
query. The chat-api still sends ingest batches **sequentially** (one HTTP call
after another); parallelism here is only across query vs passage traffic.

Set `models.warmupOnStart: true` (the YAML default) to load both lane models
into RAM during startup so the first Hugging Face request does not pay
model-load latency. Override with `WARMUP_ON_START=false` for tests and for
the OpenAI Docker path. Changing the Hugging Face model or its dimension
needs a new vector table and a re-embed. Vectors from different models are
not comparable. The API already keeps Hugging Face and OpenAI in separate
tables, so switching `EMBEDDING_PROVIDER` does not migrate old rows.

`prometheus.enabled: true` exposes `GET /metrics` on the service port (5100)
for Grafana. Counts and durations are labeled by provider (`huggingface` or
`openai`), model, and input lane. There is no separate metrics port.

## Docker

The repo README starts the whole system in three Docker steps. Set
`EMBEDDING_PROVIDER` in the root `.env` to `openai` or `huggingface`. OpenAI
is the default and does not download the local model. `huggingface` turns
prefetch and warmup on so `gte-multilingual-base` loads at container start.
The API stores that provider’s vectors in its own table.

## Setup

From the **monorepo root**:

```bash
make setup
make run
```

Or standalone:

```bash
cd apps/embedding-service
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

With prefetch left on, the first `make run` downloads ~0.6GB into the HF
cache. The first Hugging Face embed then loads that cache into RAM unless
warmup already did. Later restarts skip the download. `/health` does not load
the model. The shipped staging YAML prefetches; Docker does not, unless
`EMBEDDING_PROVIDER=huggingface`.

Set `models.prefetchOnStart: false` in `configs/staging/config.yml` to skip the
startup download (tests do this via the `PREFETCH_ON_START=false` env override).
`.env` only holds secrets (an optional `HF_TOKEN`).

## API

Swagger: http://localhost:5100/docs

```bash
curl -X POST http://localhost:5100/api/v1/embed_text \
  -H 'Content-Type: application/json' \
  -d '{"text":"What was net profit in 2024?","input_type":"query"}'

curl -X POST http://localhost:5100/api/v1/embed_texts \
  -H 'Content-Type: application/json' \
  -d '{"texts":["chunk one","chunk two"],"input_type":"passage"}'

curl -X POST http://localhost:5100/api/v1/embeddings/openai \
  -H 'Content-Type: application/json' \
  -d '{"texts":["chunk one"],"model_name":"text-embedding-3-small"}'
```

chat-api stores the vectors in Postgres/pgvector. This service has no database.
