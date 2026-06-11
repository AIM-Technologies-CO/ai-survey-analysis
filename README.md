# Survey Intelligence

A web tool over the `research` MongoDB with **two tabs**:

1. **Synthetic Data** — for any survey, generate AI answers to *new* questions on behalf of
   real respondents (grounded in their actual answers), preview on a sample, and export the
   whole cohort to Excel.
2. **Segmentation** — pick a survey (or upload an Excel), choose question labels, and an AI
   analyst (Anthropic **Agent SDK**) autonomously writes & runs pandas to build audience
   **personas**, producing a client-ready **HTML report + PowerPoint**.

Both tabs share one survey/respondent data layer and one Anthropic key.

## Architecture

- **Backend:** FastAPI (`src/server.py`) + a pymongo data layer (`src/services/data.py`).
- **Structure:** uv-managed, `src/{config,models,routes,services,utils}`, run from `src/`
  (bare absolute imports).
- **Synthetic data:** raw Anthropic API (`src/services/predictor.py`), threaded jobs →
  Excel (`src/services/synth_jobs.py`, `excelout.py`). Endpoints under `/api/*`.
- **Segmentation:** `claude-agent-sdk` agent in a per-run sandbox
  (`src/services/segmentation_agent.py`), Mongo→Excel export reusing the shared data layer
  (`segmentation_export.py`), in-memory run registry (`job_registry.py`). Endpoints under
  `/api/segmentation/*`.
- **Frontend:** vanilla HTML/JS/CSS (`src/static/`): `app.js` (synthetic data) + `seg.js`
  (tabs + segmentation), no build step.

Database `research`: a survey links to respondents via `respondents.surveyId == surveys._id`;
questions come from the survey's ordered `sections`. The eligible cohort everywhere is
**status == submitted AND exclude empty**.

## Setup

Requires **Python 3.13**, [`uv`](https://docs.astral.sh/uv/), and **Node.js** (the
segmentation Agent SDK drives the bundled Claude Code CLI).

```bash
cd /data/survey_synthetic_data_v2
uv sync
cp .env.example .env      # then fill in ANTHROPIC_API_KEY + SURVEY_MONGO_URL
```

## Run

```bash
# dev (run from inside src/ so bare imports resolve)
cd src && uv run uvicorn server:app --reload --port 8766

# production
cd src && uv run gunicorn --config utils/gunicorn_utils/gunicorn_config.py server:app
```

Open <http://localhost:8766>.

### Docker

```bash
docker compose up --build      # requires ANTHROPIC_API_KEY + SURVEY_MONGO_URL in the environment
```

## API

| Method | Path | Tab | Purpose |
|--------|------|-----|---------|
| GET | `/api/surveys?search=&limit=` | both | list/search surveys |
| GET | `/api/surveys/{id}` | synth | survey detail + flattened questions |
| GET | `/api/surveys/{id}/respondents*` | synth | respondent filtering / meta / detail |
| POST | `/api/suggest` `/api/ask` | synth | AI suggest questions / answer as respondent |
| POST | `/api/eligible-count` `/api/preview` `/api/generate-all` | synth | cohort flow |
| GET | `/api/jobs/{id}` `/api/jobs/{id}/download` | synth | generate-all job + Excel |
| GET | `/api/segmentation/surveys/{id}` | seg | counts + candidate question labels |
| POST | `/api/segmentation/upload` | seg | inspect an uploaded Excel |
| POST | `/api/segmentation/runs` | seg | start a segmentation run → job_id |
| GET | `/api/segmentation/runs/{id}?since=` | seg | poll status + progress |
| GET | `/api/segmentation/runs/{id}/report` `/pptx` | seg | the HTML report / PowerPoint |

## MCP server (Synthetic Data tab → Claude)

The synthetic-data feature is also exposed as an MCP server at **`/mcp/`** (streamable
HTTP, trailing slash), mounted inside the same process so MCP-started jobs share the
in-memory registry with the web UI. Tools: `list_surveys`, `get_survey_questions`,
`count_eligible`, `suggest_questions`, `ask_one_respondent`,
`preview_synthetic_answers` (returns `cost.projected_full_run_usd`), `generate_all`
(capped; poll with `get_job_status`, which returns a `download_url` when done).
Implementation: `src/mcp_app.py` + shared logic in `src/services/synth_service.py`.

Env vars (see `.env.example`):

- `MCP_AUTH_TOKEN` — shared secret; accepted as `Authorization: Bearer <t>` **or**
  `?token=<t>` (claude.ai connectors can only carry the URL). Empty = open endpoint;
  never deploy publicly without it.
- `PUBLIC_BASE_URL` — public origin used in `download_url`, e.g.
  `https://syntheticdata.ddns.net`.

### Connect from Claude Code

```bash
claude mcp add --transport http synthetic-data https://syntheticdata.ddns.net/mcp/ \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN"
# local dev: same command with http://localhost:8766/mcp/
```

### Connect from claude.ai (web)

1. Deploy with `MCP_AUTH_TOKEN` and `PUBLIC_BASE_URL` set (public HTTPS is required —
   already satisfied by https://syntheticdata.ddns.net).
2. claude.ai → **Settings → Connectors → Add custom connector** → URL:
   `https://syntheticdata.ddns.net/mcp/?token=<MCP_AUTH_TOKEN>`
   (trailing slash matters; leave the OAuth fields empty).
3. In a chat, enable the connector via the search-and-tools menu, then e.g.:
   *“Find the Etisalat June survey, suggest 3 churn questions, preview them and tell
   me the projected cost.”*

Security note: the token only guards `/mcp/` — the web UI and `/api/*` (including job
downloads) remain unauthenticated, as before. Verify with
`uv run python scripts/mcp_smoke.py` (add `--llm` for one cheap model call;
set `ANTHROPIC_MODEL=claude-haiku-4-5` first).

## Notes / limitations (prototype)

- **In-memory state** (synth jobs + segmentation runs) → run gunicorn with `workers=1`;
  jobs are lost on restart.
- The segmentation agent executes model-written code in the **same venv**, sandboxed to its
  `runs/<id>/` directory with web tools disabled; the report renders in a sandboxed iframe.
- Auth: locally the Agent SDK can reuse a logged-in `claude` CLI session; with
  `ANTHROPIC_API_KEY` set (recommended, and required in Docker) both features use the key.
- Segmentation cost/latency: an Opus run with `effort=high` + two deliverables takes minutes
  and a few dollars; bounded by `MAX_TURNS`, `MAX_BUDGET_USD`, `RUN_TIMEOUT_SECONDS`. Use
  `SEGMENTATION_MODEL=claude-sonnet-4-6` to iterate faster.
