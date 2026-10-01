# LangGraph supervisor for Annual Report Analyst.

Author: [Anustup Das](https://github.com/anustupdas).

Python FastAPI service (`:8080`) with one ReAct supervisor. Chat and key
datapoints share that graph. Datapoints are not a second agent: `POST /datapoints/run`
runs `mode=datapoints` with the `detail-extractor` prompt on a throwaway thread,
mutes SSE, then `supervisor_agent/extractor/main` turns the search hits into
FTE and sustainability JSON. This process returns that JSON. The API merges it
onto the document.

Prompts load from Langfuse when keys are set (`dev` then `production` in
development) and otherwise from
`src/langgraph_server/prompt/fallback_prompts/{agent}/{node}/{name}.py`.
`make langfuse-upload` pushes the local files and moves labels `production`,
`latest`, and `dev`. With keys, supervisor turns are traced.

Chat history is LangGraph Postgres checkpoints keyed by the project's
`thread_id`. The ReAct agent sees a separate `model_context_messages` window:
last 20 chat messages stay verbatim, older turns are compressed when the
window hits 50 messages or ~80k tokens. Tool JSON is not checkpointed and is
not replayed to the model on the next turn.

Design of the split: [system overview](../../docs/system-overview.md#design).

`report-search-tool` is hard-capped per turn via
`supervisor.maxReportSearchesPerTurn` (default `5` in `configs/*/config.yml`).
The system prompt steers toward 1–2 focused searches and names that budget via
`{{max_report_searches}}`.

## Run

```bash
make install-langgraph
make langgraph-up
```

Or with the rest of the stack: `make run`. The Docker path is the three steps
in the repo README. This container still needs `OPENAI_API_KEY` from the root
`.env` for chat.

- Health: http://localhost:8080/health
- Stream: `POST /supervisor/stream`
- Datapoints: `POST /datapoints/run` (`X-Agent-Secret`)
- History: `GET /supervisor/history?thread_id=&user_id=&project_id=&page=1&size=20`
  (page=1 is the newest block; scroll-up in the UI loads older pages)
- Docs: http://localhost:8080/docs

The UI (http://localhost:8000/) calls this service directly with the user Bearer token.

## Auth

Browser → `Authorization: Bearer <apiToken>`. The server checks the token via `GET /api/v1/me`,
then loads project inventory via `GET /api/v1/agent/projects/{id}` (`X-Agent-Secret`).
The search tool uses `POST /api/v1/agent/projects/{id}/chunks/search`.

## Stream body

```json
{
  "message": "What was net profit in 2024?",
  "thread_id": "<project.threadId>",
  "user_id": "<user.id>",
  "project_id": "<project.id>"
}
```

SSE events: `ReportSearchStarted`, `ReportSearched` (filename + page range),
`ConversationStarted`, `ConversationDelta`, `ConversationEnded`, then `END` / `[DONE]`.
Search events are also stored on the assistant message as `response_metadata.events`
so `/supervisor/history` can rebuild the “Sources used” card after reload.
