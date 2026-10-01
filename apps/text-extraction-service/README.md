# Text Extraction Service

Author: [Anustup Das](https://github.com/anustupdas).

FastAPI microservice for document text: **PyMuPDF by default, Mistral OCR when asked**, local disk or S3
via flags. One method is chosen for the whole file. There is no per-page OCR
heuristic. The API asks with `use_ocr`; this service only returns text and
layout. It does not write `extracted-text.txt`. That file is written by the
API during ingest. Design: [system overview](../../docs/system-overview.md#design).
Choices: [service options](../../docs/service-options.md).

## Layout

```
apps/text-extraction-service/
  configs/staging/config.yml
  docker/Dockerfile
  script/request.py
  src/text_extraction_service/
    api_v1/text_extraction_router.py
    app.py
    constants.py
    exceptions.py
    extractor.py
    metrics.py
    run.py
    service.py
    utils.py
    version.py
  tests/text_extraction_service/
  Makefile
  .env / .env.example
```

## Flags

| Source | Flag | Meaning |
|---|---|---|
| YAML | `storage.useS3` | `false` = read from `data.location` (no AWS). `true` = S3 key download. |
| YAML | `mistral.enabled` | `false` = PyMuPDF only; images rejected. `true` = OCR available. |
| Request | `use_ocr` | Whole file via Mistral (requires Mistral enabled). |
| YAML | `prometheus.enabled` | `true` exposes `GET /metrics` on the service port (5000). |

All settings live in `configs/staging/config.yml`. `.env` holds secrets only
(`MISTRAL_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`); other keys
in `.env` are ignored with a warning.

The full stack is the three Docker steps in the repo README. The root `.env`
sets `TEXT_EXTRACTION_USE_OCR`. Compose maps that to `USE_MISTRAL` in this
container. `true` needs `MISTRAL_API_KEY` in the same file.

OCR model: **`mistral-ocr-4-1`**.

### Mistral fidelity options (`mistral.*` in YAML)

| Key | Default | Effect |
|---|---|---|
| `tableFormat` | `html` | Tables as HTML (keeps merged cells); also inlined as Markdown into `text` for search. |
| `extractHeader` / `extractFooter` | `true` | Running header/footer returned separately instead of inside the body. |
| `includeImages` / `imageMinSize` / `imageLimit` | `true` / `50` / none | Page images as base64 with bounding boxes; small icons skipped. |
| `includeBlocks` | `true` | Typed layout blocks (title, text, table, image, header, footer) with bounding boxes. |
| `confidenceGranularity` | `page` | OCR confidence per page (`word` is much larger). |

### Response (`data`)

`text`, `text_format` (`markdown` for Mistral, `plain` for PyMuPDF), `method`
(`mistral`/`pymupdf`), `model`, `usage` (`pages_processed`, `doc_size_bytes`),
`duration_ms`, and `pages[]`: `page_number`, `text`, `markdown` (raw, with
table/image placeholders), `header`, `footer`, `dimensions` (width, height,
dpi), `tables`, `images`, `blocks`, `hyperlinks`, `confidence`. PyMuPDF fills
only text and dimensions.

Every request logs `extract.start … method=… model=…` and
`extract.done … pages tables images blocks chars duration_ms`.

## Setup

From the **monorepo root** (preferred):

```bash
make setup    # installs this service + DB
make run      # starts Postgres + this service on :5000
```

Or standalone:

```bash
cd apps/text-extraction-service
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

Files live under the **repo-root** `data/` directory (not `apps/.../data`):

```text
data/{user_id}/{project_id}/{doc_id}/original.pdf
```

`data.location: "../../data"` points there; relative paths resolve against this
service folder, so it works regardless of the working directory.

## Run

```bash
# from monorepo root
make run

# or locally
run-cli
# or
make debug-app
```

Swagger: http://localhost:5000/docs

API caller example (after a PDF is saved under `data/`):

```bash
curl -X POST http://localhost:5000/api/v1/extract/file \
  -H 'Content-Type: application/json' \
  -d '{"file_ref":"u1/p1/d1/original.pdf","use_ocr":false}'
```

The service returns text only. **chat-api** writes `extracted-text.txt` next to the PDF during ingest.

```text
data/{user_id}/{project_id}/{doc_id}/original.pdf
data/{user_id}/{project_id}/{doc_id}/extracted-text.txt
```

From this directory:

```bash
make test
make lint
make autoformat
```
