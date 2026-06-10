"""Per-run sandboxed working directory for the segmentation agent.

Layout:
  runs/<run_id>/
    input/survey.xlsx      # the dataset (copied in; agent reads only from here)
    INPUTS.json            # the run's parameters
    DATA_DICTIONARY.md     # column overview (+ question text when from Mongo)
    work/                  # agent scratch: scripts, intermediate CSVs, charts
    report.html            # REQUIRED final artifact
    report.pptx            # REQUIRED final artifact
    audit.jsonl            # tool-use audit trail (written by hooks)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from services import excel_inspector


@dataclass
class RunWorkspace:
    run_id: str
    run_dir: Path
    input_xlsx: Path
    work_dir: Path
    report_html: Path
    report_pptx: Path
    audit_path: Path
    data_dictionary_md: Path
    inputs_json: Path
    data_dictionary_text: str = ""

    # Paths the prompt references, relative to run_dir (the agent's cwd).
    input_rel: str = "input/survey.xlsx"
    report_html_rel: str = "report.html"
    report_pptx_rel: str = "report.pptx"
    work_rel: str = "work"


def _build_data_dictionary_md(
    input_xlsx: Path, question_text: dict[str, str] | None
) -> str:
    """Markdown column overview the agent can read before analyzing."""
    question_text = question_text or {}
    try:
        info = excel_inspector.inspect_excel(input_xlsx)
    except Exception:
        return "_(column overview unavailable; inspect the Excel directly)_"

    lines = [
        "# Data dictionary",
        "",
        f"Sheet: `{info.sheet_name}`  |  approx rows: {info.filters.estimated_rows}",
        "",
        "Detected filter columns:",
        f"- status column: `{info.filters.status_column}` (filter to value "
        f"`{info.filters.status_filter_value}`)",
        f"- exclude column: `{info.filters.exclude_column}` (drop rows where this is set)",
        "",
        "| Column | Filter? | Sample values | Question text |",
        "| --- | --- | --- | --- |",
    ]
    for col in info.columns:
        samples = ", ".join(col.sample_values[:4])
        samples = samples.replace("|", "\\|")
        qtext = (question_text.get(col.name, "") or "").replace("|", "\\|")
        if len(qtext) > 120:
            qtext = qtext[:120] + "…"
        flag = "filter" if col.is_filter else ""
        lines.append(f"| {col.name} | {flag} | {samples} | {qtext} |")
    return "\n".join(lines)


def prepare_run_dir(
    *,
    run_id: str,
    run_dir: str | Path,
    excel_path: str | Path,
    segment_by: list[str] | None,
    additional_details: str,
    question_text: dict[str, str] | None = None,
) -> RunWorkspace:
    run_dir = Path(run_dir)
    input_dir = run_dir / "input"
    work_dir = run_dir / "work"
    for d in (input_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)

    input_xlsx = input_dir / "survey.xlsx"
    excel_path = Path(excel_path)
    if excel_path.resolve() != input_xlsx.resolve():
        shutil.copyfile(excel_path, input_xlsx)

    ws = RunWorkspace(
        run_id=run_id,
        run_dir=run_dir,
        input_xlsx=input_xlsx,
        work_dir=work_dir,
        report_html=run_dir / "report.html",
        report_pptx=run_dir / "report.pptx",
        audit_path=run_dir / "audit.jsonl",
        data_dictionary_md=run_dir / "DATA_DICTIONARY.md",
        inputs_json=run_dir / "INPUTS.json",
    )

    ws.inputs_json.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "input_file": ws.input_rel,
                "segment_by": segment_by or [],
                "additional_details": additional_details,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    ws.data_dictionary_text = _build_data_dictionary_md(input_xlsx, question_text)
    ws.data_dictionary_md.write_text(ws.data_dictionary_text, encoding="utf-8")

    return ws
