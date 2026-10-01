# Service options

Author: [Anustup Das](https://github.com/anustupdas).

What each process can be set to, and which combinations actually run. Why the
processes are split the way they are is in
[system overview](system-overview.md#design). Docker
and `make run` both start from `configs/staging/config.yml`. A `production/`
copy sits next to the API, embedding, and LangGraph files; point a process at
it with `API_CONFIG_PATH`, `LANGGRAPH_CONFIG_PATH`, or `REMOTE_CONFIG_URL`.

How a setting is applied:

| How you run | Where the choice lives |
|---|---|
| Docker | Root `.env`. Compose injects it as process environment, which overrides the YAML baked into the image. |
| `make run` | Per-app `.env` for secrets. Non-secret keys in those files are ignored. Change `configs/staging/config.yml`, or export a real process variable. |

After you edit `.env` for Docker, start again with
`docker compose --env-file .env up --build`.

## Combinations

Chat always uses OpenAI (`gpt-6-luna` in the shipped prompts). The embedding
choice does not change that. `OPENAI_API_KEY` is required in every row.

| Text extraction | Embeddings | Langfuse | What to set |
|---|---|---|---|
| PyMuPDF | OpenAI | off | `OPENAI_API_KEY` only. This is the default. |
| Mistral OCR | OpenAI | off | `TEXT_EXTRACTION_USE_OCR=true` and `MISTRAL_API_KEY` |
| PyMuPDF | Hugging Face | off | `EMBEDDING_PROVIDER=huggingface` |
| Mistral OCR | Hugging Face | off | both switches above, plus `MISTRAL_API_KEY` |
| any of the above | any of the above | on | add `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_HOST` |

`DATA_DIR` (default `./data`) is the host folder mounted at `/data` for the API
and text extraction. Reports already on disk stay where they are if you set
that path.

## Text extraction (`:5000`)

Two ways to read a file. The API flag and the extraction service must agree.
Docker maps one root key onto both.

| | PyMuPDF | Mistral OCR |
|---|---|---|
| Docker | `TEXT_EXTRACTION_USE_OCR=false` | `TEXT_EXTRACTION_USE_OCR=true` and `MISTRAL_API_KEY` |
| YAML | `textExtraction.useOcr: false` and `mistral.enabled: false` | `textExtraction.useOcr: true` and `mistral.enabled: true` |
| Process env on the extractor | `USE_MISTRAL=false` | `USE_MISTRAL=true` and `MISTRAL_API_KEY` |
| Result | Local text from the PDF. No key. | Whole file through `mistral-ocr-4-1` |

PyMuPDF is plain text. Mistral returns markdown, HTML tables, headers,
footers, and layout blocks. Image uploads (png, jpg, and the other formats
under `files.imageFormats`) require Mistral. A PDF with OCR requested while
Mistral is off fails instead of silently falling back.

Other extractor settings in `apps/text-extraction-service/configs/staging/config.yml`:

- `storage.useS3: false` reads `data.location` (`../../data`, or `LOCAL_DATA_DIR` / Docker `/data`). `true` needs `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `storage.bucket`.
- `mistral.tableFormat`, `extractHeader`, `extractFooter`, `includeImages`, `imageMinSize`, `includeBlocks`, and `confidenceGranularity` (`page`, `block`, or `word`) apply only when Mistral runs.

## Embedding (`:5100`)

Two providers. Vectors are stored in separate tables. Changing provider does
not rewrite vectors already stored; process those reports again.

| | OpenAI | Hugging Face |
|---|---|---|
| Docker | `EMBEDDING_PROVIDER=openai` | `EMBEDDING_PROVIDER=huggingface` |
| Aliases | `oai` | `hf`, `local`, `gte` |
| Model | `text-embedding-3-small` | `gte-multilingual-base` (`Alibaba-NLP/gte-multilingual-base`) |
| Dimension | 1536 | 768 |
| API route | `POST /api/v1/embeddings/openai` | `POST /api/v1/embed_texts` (passage) and `/embed_text` (query) |
| Postgres table | `document_chunk_embeddings_openai` | `document_chunk_embeddings_hf` |
| Startup | Model is not downloaded | Docker sets prefetch and warmup on, so the model loads at start |

The API refuses to start if `embedding.dimension` does not match the provider.
The Docker entrypoint sets the model name and dimension from
`EMBEDDING_PROVIDER`, so the root `.env` only needs that one key.

Hugging Face runs two in-memory copies: a query lane for search and a passage
lane for ingest. Two reports ingested at once share the passage lane. OpenAI
embeddings do not use those lanes. `HF_TOKEN` is optional and only needed for
a gated Hub model. `multilingual-e5-large` is in the YAML as a reference and
is not enabled.

YAML keys: `embedding.provider`, `embedding.modelName`, `embedding.dimension`
on the API; `models.enabled`, `models.prefetchOnStart`, `models.warmupOnStart`
on the embedding service. `PREFETCH_ON_START` and `WARMUP_ON_START` override
the embedding YAML.

## Chat API (`:8000`)

Owns users, projects, ingest, and the UI in `apps/web`. It calls extraction
and embedding, then asks LangGraph for key datapoints.

Settings that matter next to the choices above, all in
`apps/api/configs/staging/config.yml`:

| Key | Default | Role |
|---|---|---|
| `storage.dataRoot` | `../../data` | Upload directory. Docker sets `DATA_ROOT=/data`. |
| `storage.maxUploadBytes` | 100 MiB | Upload limit |
| `chunking.targetChars` / `maxChars` / `overlapSentences` | 2000 / 2500 / 2 | Chunk size |
| `vectorSearch.defaultLimit` / `maxLimit` | 8 / 50 | Search page size |
| `llm.summaryEnabled` | true | Company, year, and summary via OpenAI. Empty `OPENAI_API_KEY` skips the step. |
| `llm.descriptionContextChunks` | 10 | Opening chunks sent to that description |
| `langgraph.url` | `http://localhost:8080` | Address the browser uses |
| `langgraph.internalUrl` | empty | Datapoints. Docker sets `LANGGRAPH_INTERNAL_URL=http://langgraph:8080` |

`AGENT_SECRET` must match LangGraph. `INTERNAL_WORKFLOW_SECRET` is reserved
for internal workers.

## LangGraph (`:8080`)

One ReAct supervisor. Chat streams on `POST /supervisor/stream`. Datapoints
call `POST /datapoints/run` with `X-Agent-Secret`. Checkpoints live in the
same Postgres database (`langgraph.checkpoint: postgres`; `memory` is the
other value, for tests).

| Key | Default | Role |
|---|---|---|
| `supervisor.maxReportSearchesPerTurn` | 5 | Hard cap on `report-search-tool` calls per turn |
| `vectorSearch.defaultLimit` | 8 | Chunks per search when the tool omits a limit |
| `supervisor.summarizationEnabled` | true | Compress older chat turns |
| `supervisor.summarizationTriggerMessages` | 50 | When to compress |
| `supervisor.summarizationKeepMessages` | 20 | Turns left verbatim |
| `supervisor.summarizationTriggerTokens` | 80000 | Token trigger |
| `supervisor.summarizationTrimTokens` | 24000 | Target after trim |
| `langgraph.sseKeepaliveSeconds` | 15 | SSE keepalive |

`api.url` is `http://localhost:8000` on the host and `http://api:8000` inside
Compose.

## Langfuse

Tracing is on in the staging YAML (`langfuse.tracing: true`). With the three
keys empty, the API and LangGraph log a warning and use the prompts shipped
in the repo. Nothing else is required.

To trace into your own project:

1. Create a project at [cloud.langfuse.com](https://cloud.langfuse.com).
2. Copy that project's secret key, public key, and host into `.env`:

```bash
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

3. Start again. Chat, document description, and the datapoints extractor then
   show up as traces. Prompts are read from that project when a labeled
   version exists, and from the repo when it does not.

Prompt labels follow `langgraph.environment` / `api.environment`.
`development` tries `dev`, then `production`. `production` uses `production`
only. Cached for `langfuse.promptCacheTtlSeconds` (60).

One command pushes every local prompt and moves the `production`, `latest`,
and `dev` labels onto the new versions:

```bash
make langfuse-upload
```

That needs the keys in the root `.env` or in `apps/api/.env` (LangGraph also
reads the API file). It uploads:

| Prompt | Used for | Shipped model config |
|---|---|---|
| `ingest/describe_document/main` | Company, year, summary | `gpt-6-luna`, 2000 output tokens |
| `supervisor_agent/supervisor/main` | Analyst chat | `gpt-6-luna`, 4000 output tokens |
| `supervisor_agent/supervisor/detail-extractor` | Datapoints search turn | `gpt-6-luna`, 2000 output tokens |
| `supervisor_agent/extractor/main` | FTE and sustainability JSON | `gpt-6-luna`, 8000 output tokens |

The extractor cap is 8000 because reasoning tokens count against
`max_output_tokens`. A lower cap can finish with no JSON.

## Postgres (`:5432`)

`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` default to `chat`,
`chat`, and `chat_project`. The data volume is created on first start.
Migrations run once in the `migrate` container before the API and LangGraph
start.
