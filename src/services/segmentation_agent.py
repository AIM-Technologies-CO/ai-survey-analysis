"""The segmentation agent engine.

Runs Claude (via claude-agent-sdk) in a per-run sandbox where it autonomously writes
and executes pandas code to explore the survey, derive personas, and author the
report.html + report.pptx deliverables.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    query,
)

from config import settings
from models.segmentation import ProgressEvent, RunStatus, SegmentationResult
from services import run_workspace, segmentation_sanitize
from services.prompts import SYSTEM_APPEND, build_agent_definitions, build_task_prompt
from utils.audit import AuditTrail
from utils.logging_config.logger import get_logger

logger = get_logger()

OnEvent = Callable[[ProgressEvent], Awaitable[None]]

SENTINEL = "SEGMENTATION_COMPLETE"
_ERROR_SUBTYPES = {"error_max_turns", "error_during_execution", "error_max_budget_usd"}


def _friendly_file(path: str) -> str:
    """Describe a file the agent touches in plain language for the activity log."""
    fname = Path(str(path)).name
    if not fname:
        return "a working file"
    low = fname.lower()
    if low == "personas.json":
        return "the audience personas"
    if low == "report.html":
        return "the HTML report"
    if low == "report.pptx":
        return "the PowerPoint deck"
    if low.endswith(".png"):
        return f"a chart ({fname})"
    if low.endswith(".py"):
        return f"an analysis script ({fname})"
    if low.endswith((".xlsx", ".xls", ".csv")):
        return f"the survey data ({fname})"
    if low.endswith((".md", ".json", ".txt")):
        return f"a working file ({fname})"
    return fname


def _summarize_tool_input(name: str | None, ti: dict) -> str:
    """Translate a raw agent tool call into a plain-English line a non-technical
    reader can follow. The full, exact input is still kept in the audit trail."""
    if name == "Bash":
        cmd = str(ti.get("command", "")).strip()
        low = cmd.lower()
        if any(k in low for k in ("pip install", "uv pip", "uv add", "pip3 install")):
            return "Installing a Python library it needs"
        if low.startswith(("python", "python3")) or " python " in low:
            script = next((Path(t).name for t in cmd.split() if t.endswith(".py")), None)
            return f"Running its analysis code ({script})" if script else "Running its analysis code"
        return "Working in the analysis sandbox"
    if name == "Write":
        return f"Saving {_friendly_file(ti.get('file_path', ''))}"
    if name == "Edit":
        return f"Refining {_friendly_file(ti.get('file_path', ''))}"
    if name == "Read":
        return f"Reviewing {_friendly_file(ti.get('file_path', ''))}"
    if name in ("Glob", "Grep"):
        return "Looking through the workspace files"
    if name in ("Agent", "Task"):
        sub = (ti.get("subagent_type") or "").lower()
        if "html" in sub:
            return "Handing off to the report designer to build the HTML report"
        if "pptx" in sub or "deck" in sub:
            return "Handing off to the deck designer to build the PowerPoint"
        return "Handing work to a specialist builder"
    return f"Working… ({name})" if name else "Working…"


async def run_segmentation(
    *,
    run_dir: str | Path,
    excel_path: str | Path,
    segment_by: list[str] | None = None,
    additional_details: str = "",
    question_text: dict[str, str] | None = None,
    waves: list[dict] | None = None,
    on_event: OnEvent | None = None,
    run_id: str | None = None,
) -> SegmentationResult:
    run_dir = Path(run_dir)
    run_id = run_id or run_dir.name
    audit = AuditTrail(run_dir / "audit.jsonl")

    async def emit(kind: str, message: str, tool_name: str | None = None, data: dict | None = None) -> None:
        if on_event:
            try:
                await on_event(ProgressEvent(kind=kind, message=message, tool_name=tool_name, data=data))
            except Exception:
                logger.exception("on_event callback raised")

    # --- prepare sandbox ---
    await emit("status", "Preparing run workspace…")
    ws = run_workspace.prepare_run_dir(
        run_id=run_id,
        run_dir=run_dir,
        excel_path=excel_path,
        segment_by=segment_by,
        additional_details=additional_details,
        question_text=question_text,
    )
    task_prompt = build_task_prompt(
        input_rel=ws.input_rel,
        report_html_rel=ws.report_html_rel,
        report_pptx_rel=ws.report_pptx_rel,
        work_rel=ws.work_rel,
        segment_by=segment_by,
        additional_details=additional_details,
        data_dictionary_md=ws.data_dictionary_text,
        waves=waves,
    )

    state = {"session_id": None, "num_turns": 0, "cost": None, "subtype": None, "errors": None, "sentinel": False}

    # --- hooks (observability only; never block) ---
    async def pre_tool_hook(input_data, tool_use_id, context):
        try:
            name = input_data.get("tool_name")
            ti = input_data.get("tool_input", {}) or {}
            audit.append({"event": "pre_tool", "tool": name, "input": ti, "id": tool_use_id})
            await emit("tool_use", _summarize_tool_input(name, ti), tool_name=name)
        except Exception:
            logger.exception("pre_tool_hook error")
        return {}

    async def post_tool_hook(input_data, tool_use_id, context):
        try:
            name = input_data.get("tool_name")
            audit.append({"event": "post_tool", "tool": name, "id": tool_use_id})
        except Exception:
            logger.exception("post_tool_hook error")
        return {}

    # Headless matplotlib + force API-key auth when configured (overrides any inherited
    # CLI subscription session, since options.env always wins for the spawned CLI).
    agent_env = {"MPLBACKEND": "Agg"}
    if settings.anthropic_api_key:
        agent_env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

    options = ClaudeAgentOptions(
        cwd=str(ws.run_dir),
        add_dirs=[],
        setting_sources=[],
        system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_APPEND},
        allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Agent"],
        disallowed_tools=["WebSearch", "WebFetch"],
        agents=build_agent_definitions(),  # html-report-builder + pptx-deck-builder (parallel)
        permission_mode="bypassPermissions",
        model=settings.segmentation_model,
        fallback_model=settings.fallback_model,
        effort="high",
        max_turns=settings.max_turns,
        max_budget_usd=settings.max_budget_usd,
        env=agent_env,
        hooks={
            "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_hook])],
            "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_hook])],
        },
    )

    async def drive(prompt: str, opts: ClaudeAgentOptions) -> None:
        async for message in query(prompt=prompt, options=opts):
            if isinstance(message, SystemMessage):
                if message.subtype == "init":
                    state["session_id"] = message.data.get("session_id", state["session_id"])
                    await emit("status", "Agent session initialized")
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = (block.text or "").strip()
                        if SENTINEL in text:
                            state["sentinel"] = True
                        if text:
                            await emit("assistant_text", text[:1500])
            elif isinstance(message, ResultMessage):
                state["subtype"] = message.subtype
                state["num_turns"] = message.num_turns
                state["cost"] = message.total_cost_usd
                state["session_id"] = message.session_id or state["session_id"]
                state["errors"] = message.errors

    def _artifacts_ok() -> tuple[bool, bool]:
        html_ok = ws.report_html.exists() and ws.report_html.stat().st_size > 0
        pptx_ok = ws.report_pptx.exists() and ws.report_pptx.stat().st_size > 0
        return html_ok, pptx_ok

    async def finalize(status: RunStatus, error: str | None) -> SegmentationResult:
        html_ok, pptx_ok = _artifacts_ok()
        result = SegmentationResult(
            run_id=run_id,
            status=status,
            report_html_path=str(ws.report_html) if html_ok else None,
            report_pptx_path=str(ws.report_pptx) if pptx_ok else None,
            session_id=state["session_id"],
            num_turns=state["num_turns"],
            total_cost_usd=state["cost"],
            error=error,
            audit_path=str(ws.audit_path),
        )
        audit.append({"event": "finalize", "status": status.value, "error": error, "cost": state["cost"]})
        await emit(
            "result",
            f"Finished: {status.value} ({state['num_turns']} turns"
            + (f", ${state['cost']:.2f}" if state["cost"] else "") + ")",
            data={"status": status.value, "cost_usd": state["cost"], "num_turns": state["num_turns"]},
        )
        return result

    # --- run ---
    await emit("status", "Running segmentation agent…")
    logger.info("Run %s started (model=%s)", run_id, settings.segmentation_model)
    try:
        await asyncio.wait_for(drive(task_prompt, options), timeout=settings.run_timeout_seconds)
    except asyncio.TimeoutError:
        return await finalize(RunStatus.timed_out, f"wall-clock timeout ({settings.run_timeout_seconds}s)")
    except Exception as exc:
        logger.exception("Agent run failed for %s", run_id)
        return await finalize(RunStatus.failed, str(exc))

    html_ok, pptx_ok = _artifacts_ok()

    # one bounded retry if the agent reported success but an artifact is missing
    if state["subtype"] == "success" and not (html_ok and pptx_ok) and state["session_id"]:
        missing = [rel for rel, ok in [(ws.report_html_rel, html_ok), (ws.report_pptx_rel, pptx_ok)] if not ok]
        await emit("status", f"Missing artifact(s) {missing} — retrying once")
        retry_prompt = (
            f"The required output file(s) {missing} are missing or empty in your working "
            f"directory. Produce them now exactly as specified: a self-contained "
            f"{ws.report_html_rel} and {ws.report_pptx_rel}. End with {SENTINEL} when done."
        )
        retry_opts = dataclasses.replace(options, resume=state["session_id"])
        try:
            await asyncio.wait_for(drive(retry_prompt, retry_opts), timeout=settings.run_timeout_seconds)
        except Exception as exc:
            audit.append({"event": "retry_failed", "error": str(exc)})
        html_ok, pptx_ok = _artifacts_ok()

    if html_ok and pptx_ok:
        # Safety net: strip any em/en dashes the model slipped past the prompt rule.
        try:
            counts = await asyncio.to_thread(
                segmentation_sanitize.sanitize_artifacts, ws.report_html, ws.report_pptx
            )
            audit.append({"event": "dash_sanitize", **counts})
            total = counts["html"] + counts["pptx"]
            if total:
                await emit("status", f"Cleaned {total} stray dash(es) from the deliverables "
                                     f"(HTML {counts['html']}, PPTX {counts['pptx']}).")
        except Exception:
            logger.exception("dash sanitize failed for %s", run_id)
        return await finalize(RunStatus.succeeded, None)
    if state["subtype"] in _ERROR_SUBTYPES:
        err = f"agent ended with {state['subtype']}"
        if state["errors"]:
            err += f": {state['errors']}"
        return await finalize(RunStatus.failed, err)
    missing = [rel for rel, ok in [(ws.report_html_rel, html_ok), (ws.report_pptx_rel, pptx_ok)] if not ok]
    return await finalize(RunStatus.artifacts_missing, f"missing artifacts: {missing}")
