# Web UI — Annual Report Analyst

Author: [Anustup Das](https://github.com/anustupdas).

Zero-build SPA served by the API at http://localhost:8000/ (same origin as `/api/v1`).
There is no bundler. The API image copies these files and serves them. Ingest
and document state go to the API. Analyst chat goes straight to LangGraph at
the address in `GET /config.js` (`http://localhost:8080` in Docker and on the
host). The transcript is the project's LangGraph checkpoint, not a table in
this app. Design: [system overview](../../docs/system-overview.md#design).
The Docker start is the three steps in the repo README.

```
Sign-in and home  /          Auth, then report projects
Workspace         /project/{id}   reports | report canvas | report analyst
```

Static files: `index.html`, `styles.css`, `app.js`. The API serves them with `Cache-Control: no-store`. Bump the `?v=` query on the stylesheet and script when you change them.

## Privacy warning

The sign-in page and the signed-in home both show the same note: do not upload personal documents. Text from uploaded files can be sent to an external LLM, and no GDPR guardrail is in place yet. Upload public documents only.

## Theme

Light mode uses a gray page (`#f0f2f5`), white cards, and a blue accent (`#1877f2`). Dark mode sets `theme-dark` on `html`. The Dark mode / Light mode button on the sign-in page, the home top bar, and the workspace top bar stores `light` or `dark` in `localStorage` as `report_rag_theme`. With nothing stored, the page follows `prefers-color-scheme`. A short script in `index.html` applies the class before the first paint.

## Wired

- Create account, paste token, regenerate token (shown once)
- Privacy warning on sign-in and on the signed-in home
- Dark mode, stored in `report_rag_theme`
- Home cards and project switcher → `/project/{id}`
- Upload: reports `+`, canvas **Upload annual report**, analyst `+`, or drag a PDF onto the reports pane
- Document status polling (`pending` → `processing` → `ready` → `completed` / `failed`)
- Report canvas: company, report year, summary, and FTE / sustainability key datapoints
- Document type stays empty until classification exists (`Detecting…` while `ready`, `Not detected` once `completed`)
- Example questions and quick prompts (Summarize / FTE count / Sustainability goals) fill the composer
- Hide reports / hide analyst / maximize analyst
- Analyst chat streams from LangGraph (`/supervisor/stream`) using the project `threadId`
- Search status and source pages (`ReportSearchStarted` / `ReportSearched`) show above the answer
- Session IDs (`userId`, `projectId`, `threadId`) in a collapsible **Session** row

## Files

| File | Role |
|---|---|
| `index.html` | Auth, home, workspace shell, privacy warning, theme bootstrap |
| `styles.css` | Light and dark tokens, 3-pane layout, report canvas, pane scroll |
| `app.js` | Hashless routes, ingest, LangGraph analyst chat, theme, chrome |
