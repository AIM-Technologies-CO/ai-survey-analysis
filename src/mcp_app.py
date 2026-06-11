"""MCP server for the Synthetic Data feature, mounted at /mcp/ by server.py.

Same process as the web UI, so MCP-started jobs share the in-memory registry in
services/synth_jobs.py (this is why it is mounted, not run standalone — and why
gunicorn must stay at workers=1). Tools are thin async wrappers over
services/synth_service.py; blocking Mongo/Anthropic work runs in worker threads
because FastMCP calls tools on the event loop.

Auth: static token from MCP_AUTH_TOKEN, accepted as `Authorization: Bearer <t>`
(Claude Code --header) or `?token=<t>` in the URL (claude.ai custom connectors
only take a URL). Unset token == open endpoint, for localhost dev only.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import Any, Literal
from urllib.parse import parse_qs

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from config import settings
from services import data, predictor, result_store
from services import synth_jobs as jobs
from services import synth_service as synth

MCP_SESSION = "mcp"  # session_id used in tracking logs for MCP-originated actions

mcp = FastMCP(
    name="survey-synthetic-data",
    instructions=(
        "Generate AI answers to NEW questions on behalf of real survey respondents, "
        "grounded in their actual answers, and export the cohort to Excel. "
        "IMPORTANT: never produce survey content yourself — every count, question and "
        "answer MUST come from these tools, which run against the live survey database. "
        "In particular, when the user asks to generate / suggest / draft / brainstorm "
        "new questions, ALWAYS call suggest_questions instead of writing questions "
        "yourself: it knows the survey's existing questions and grounds suggestions in "
        "them. Only pass user-authored questions directly to preview/generate tools "
        "when the user explicitly wrote the questions themselves. "
        "Workflow: list_surveys -> get_survey_questions -> count_eligible -> "
        "suggest_questions -> preview_synthetic_answers -> confirm the projected cost "
        "with the user -> generate_all -> poll get_job_status until done. "
        "Each survey has ONE living workspace page: every tool result's view_url for "
        "the same survey is the same link, and the page gains sections (questions, "
        "preview, job progress) as you work. Share the link when it first appears and "
        "remind the user it updates live — do not present it as a new link each time."
    ),
    streamable_http_path="/",  # mounted at /mcp -> canonical endpoint is /mcp/
    stateless_http=True,
    json_response=True,
    # token auth replaces Host-header allowlisting; without this, requests via
    # tunnels/LAN are rejected by the localhost-only DNS-rebinding default
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_READ_LLM = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
_SPENDS = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                          idempotentHint=False, openWorldHint=True)


class AdHocQuestion(BaseModel):
    """A new question to ask on behalf of respondents."""
    text: str
    type: Literal["multipleChoice", "checkBoxes", "shortAnswer", "numericAnswer"]
    options: list[str] = []  # required (>=2) for multipleChoice / checkBoxes
    id: int | str | None = None


async def _run(fn, *args, **kwargs):
    """Run a blocking service call off the event loop, mapping errors to ToolError."""
    def call():
        try:
            return fn(*args, **kwargs)
        except (ValueError, RuntimeError) as e:
            raise ToolError(str(e))
        except KeyError as e:
            raise ToolError(str(e.args[0]) if e.args else "not found")
        except PyMongoError as e:
            raise ToolError(f"database unavailable: {e}")
    return await anyio.to_thread.run_sync(call)


def _qdicts(questions: list[AdHocQuestion]) -> list[dict]:
    return [q.model_dump() for q in questions]


def _survey_name(survey_id: str) -> str:
    try:
        return data.get_survey(survey_id).get("name") or survey_id
    except Exception:
        return survey_id


def _workspace(survey_id: str, section: str, data,
               merge_questions: list[dict] | None = None) -> str:
    """Upsert a section of the survey's living workspace page; returns its URL.

    One survey == one link: successive tool calls update the same page (questions,
    then preview, then job), mirroring the web UI's stepped flow."""
    wid = result_store.upsert_workspace(survey_id, _survey_name(survey_id),
                                        section, data, merge_questions=merge_questions)
    return result_store.view_url(wid)


@mcp.tool(annotations=_READ_ONLY)
async def list_surveys(search: str | None = None, limit: int = 20) -> dict[str, Any]:
    """List surveys in the research database, optionally filtered by a
    case-insensitive name search. Start here to find a survey_id."""
    surveys = await _run(data.list_surveys, search, max(1, min(limit, 200)))
    return {"surveys": [asdict(s) for s in surveys]}


@mcp.tool(annotations=_READ_ONLY)
async def get_survey_questions(survey_id: str) -> dict[str, Any]:
    """The survey's existing questions (ordered, with type/options/section) plus the
    submit-date bounds of its respondents. Use to understand what was already asked
    before drafting new questions, and to pick date filters."""
    return await _run(synth.survey_questions, survey_id)


@mcp.tool(annotations=_READ_ONLY)
async def count_eligible(survey_id: str, date_from: str | None = None,
                         date_to: str | None = None, include_all: bool = False) -> dict[str, Any]:
    """Count respondents eligible for generation (status=submitted, non-empty),
    optionally filtered by submit date (YYYY-MM-DD, date_to is inclusive).
    Returns the eligible count and the per-job respondent cap."""
    return await _run(synth.eligible_count, survey_id, date_from=date_from,
                      date_to=date_to, include_all=include_all, session_id=MCP_SESSION)


@mcp.tool(annotations=_READ_LLM)
async def suggest_questions(survey_id: str, n: int = 5, focus: str | None = None,
                            already: list[str] | None = None) -> dict[str, Any]:
    """REQUIRED whenever the user asks to generate, suggest, draft, brainstorm or
    refine NEW survey questions — never write questions yourself; this endpoint knows
    the survey's existing questions and avoids duplicating them. Proposes n (1-12)
    questions with type/options/rationale (one cheap LLM call). Pass the user's theme
    as `focus` (e.g. "churn drivers"); pass texts of questions already drafted in this
    conversation as `already` to avoid repeats. Returns view_url — the survey's live
    workspace page where the questions appear (the SAME link updates as you continue
    to preview/generate); share it with the user once."""
    def run():
        data.get_survey(survey_id)
        synth.require_api_key()
        out = predictor.suggest_questions(survey_id, n=max(1, min(n, 12)),
                                          already=already or [], focus=focus)
        new_qs = [{**q, "source": "ai"} for q in out["questions"]]
        out["view_url"] = _workspace(survey_id, "questions", None, merge_questions=new_qs)
        return out
    return await _run(run)


@mcp.tool(annotations=_READ_LLM)
async def ask_one_respondent(survey_id: str, respondent_id: str,
                             questions: list[AdHocQuestion]) -> dict[str, Any]:
    """Generate answers to the given questions for ONE respondent, grounded in their
    real answers. Each answer includes the reasoning and the real answers it was
    derived from (references), plus usage.cost_usd for this single call. Returns
    view_url — a shareable page rendering the answers; include it in your reply."""
    def run():
        respondent = data.get_respondent(survey_id, respondent_id)
        cleaned = synth.clean_questions(_qdicts(questions))
        synth.require_api_key()
        out = predictor.ask_ad_hoc(survey_id, respondent, cleaned)
        out["view_url"] = _workspace(survey_id, "ask",
                                     {"respondent_id": respondent_id,
                                      "model": out.get("model"), "answers": out["answers"]},
                                     merge_questions=[{**q, "source": "manual"} for q in cleaned])
        return out
    return await _run(run)


@mcp.tool(annotations=_READ_LLM)
async def preview_synthetic_answers(survey_id: str, questions: list[AdHocQuestion],
                                    date_from: str | None = None, date_to: str | None = None,
                                    include_all: bool = False) -> dict[str, Any]:
    """Generate answers for a small sample (~10 respondents; may take ~1 minute) so
    quality and cost can be judged before committing. Returns per-respondent results
    (with real answers alongside) and cost.projected_full_run_usd for the capped full
    run. ALWAYS run this before generate_all and confirm the projected cost with the
    user. Check that generated answers are grounded in each respondent's real ones.
    Returns view_url — the survey's live workspace page, now updated with a preview
    section (same link as before for this survey); share it with the user."""
    def run():
        qd = _qdicts(questions)
        out = synth.run_preview(survey_id, qd,
                                date_from=date_from, date_to=date_to,
                                include_all=include_all, session_id=MCP_SESSION)
        out["view_url"] = _workspace(survey_id, "preview", out,
                                     merge_questions=[{**q, "source": "manual"} for q in qd])
        return out
    return await _run(run)


@mcp.tool(annotations=_SPENDS)
async def generate_all(survey_id: str, questions: list[AdHocQuestion],
                       date_from: str | None = None, date_to: str | None = None,
                       include_all: bool = False) -> dict[str, Any]:
    """Start the full background generation job over the eligible cohort (capped) and
    export to Excel. SPENDS REAL MONEY — one LLM call per respondent: run
    preview_synthetic_answers first and confirm the projected cost with the user.
    Returns the job immediately; poll get_job_status with the returned id. Also
    returns view_url — the survey's live workspace page, now with a job-progress
    section and the Excel download once done (same link as before for this survey)."""
    def run():
        qd = _qdicts(questions)
        view = synth.start_generate_all(survey_id, qd,
                                        date_from=date_from, date_to=date_to,
                                        include_all=include_all, session_id=MCP_SESSION)
        view["view_url"] = _workspace(survey_id, "job",
                                      {"job_id": view["id"],
                                       "filter": {"date_from": date_from, "date_to": date_to,
                                                  "include_all": include_all}},
                                      merge_questions=[{**q, "source": "manual"} for q in qd])
        return view
    return await _run(run)


@mcp.tool(annotations=_READ_ONLY)
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Progress of a generate_all job (done/ok/failed counts). Poll every ~10s until
    state is 'done' or 'error'. When done, includes download_url for the Excel and
    the file path on the server."""
    view = await _run(synth.job_view, job_id)
    if view.get("state") == "done" and view.get("filename"):
        view["download_url"] = f"{result_store.base_url()}/api/jobs/{job_id}/download"
        try:
            view["exports_path"], _ = synth.job_file(job_id)
        except (KeyError, ValueError):
            pass
    job = jobs.get_job(job_id)
    if job:
        wid = result_store.workspace_id(job.get("survey_id", ""))
        if wid:
            view["view_url"] = result_store.view_url(wid)
    return view


class TokenAuthMiddleware:
    """ASGI wrapper: require the shared token as a Bearer header or ?token= param."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        expected = f"Bearer {self.token}"
        ok = any(k == b"authorization" and hmac.compare_digest(v.decode("latin-1"), expected)
                 for k, v in scope.get("headers") or [])
        if not ok:
            qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            ok = any(hmac.compare_digest(t, self.token) for t in qs.get("token", []))
        if not ok:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def build_mcp_asgi_app():
    """The /mcp sub-app. Must be called at import time: it instantiates the session
    manager that server.py's lifespan then runs (mounted sub-app lifespans don't run)."""
    return TokenAuthMiddleware(mcp.streamable_http_app(), settings.mcp_auth_token)
