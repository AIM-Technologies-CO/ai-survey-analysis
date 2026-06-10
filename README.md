# Survey Synthetic Data — v2 (live from MongoDB)

A web tool that, for any survey in the `research` database, lets you:

1. **Search & pick a survey** by name (live query).
2. **Filter & pick a respondent** (status, answered-count range, id search).
3. **Generate questions for the survey with AI** (or compose your own) and have AI
   **answer them as the selected respondent**, grounded in that respondent's real answers.

Unlike v1, this version reads **directly from MongoDB** — there is no export step and no
JSON files on disk. The "hide a question and predict it" feature from v1 has been removed.

## Architecture

- **Backend:** FastAPI (`app/server.py`) + pymongo data layer (`app/data.py`).
- **AI:** Anthropic Claude (`app/predictor.py`) — `suggest_questions` + `ask_ad_hoc`.
- **Frontend:** vanilla HTML/JS/CSS (`app/static/`), no build step.

Database: `research` (collections `surveys`, `sections`, `respondents`). A survey links to
respondents via `respondents.surveyId == surveys._id`; questions come from the survey's
ordered `sections`.

## Setup

```bash
cd /data/survey_synthetic_data_v2
pip install -r requirements.txt
```

Provide your Anthropic key and the Mongo URL (model is optional). Either export them:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export SURVEY_MONGO_URL=mongodb://user:password@host:port/research
# optional:
export ANTHROPIC_MODEL=claude-opus-4-7
```

…or copy `.env.example` to `app/.env` (it is loaded automatically on startup).

## Run

```bash
python3 -m uvicorn app.server:app --host 127.0.0.1 --port 8766
```

Then open <http://127.0.0.1:8766>.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/surveys?search=&limit=` | List/search surveys by name |
| GET | `/api/surveys/{id}` | Survey detail + flattened questions |
| GET | `/api/surveys/{id}/respondents-meta` | Status histogram, min/max answers, total |
| GET | `/api/surveys/{id}/respondents?status=&min_answers=&max_answers=&limit=` | Filtered random sample |
| GET | `/api/surveys/{id}/respondents/{rid}` | One respondent's answers |
| POST | `/api/suggest` `{survey_id, n}` | AI-generate new questions for the survey |
| POST | `/api/ask` `{survey_id, respondent_id, questions[]}` | AI answers as the respondent |

## Notes

- `respondents-meta` and respondent filtering scan a survey's respondents (the answered-count
  is computed with `$size`, which can't be indexed). `respondents-meta` is cached for 60s.
  An index on `respondents.surveyId` (ideally `{surveyId:1, status:1}`) keeps the initial
  `$match` fast — confirm it exists for large surveys.
- Survey docs, sections, and flattened questions are cached in-process; restart the server
  to pick up survey-definition edits.
- The connection string is required via `SURVEY_MONGO_URL` (the server raises a clear error if it
  is unset). Set it (and the API key) via env/`.env` — never commit secrets.
