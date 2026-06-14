"""Use Claude to (1) suggest new questions for a survey and (2) answer questions
as a specific respondent, grounded in that respondent's real survey answers."""
from __future__ import annotations

import json
import os
from typing import Any

import anthropic

from services.data import QuestionMeta, get_question, get_survey_questions, respondent_answers_by_qid

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")
MAX_TOKENS = 1500

# USD per 1M tokens, (input, output). Source: Anthropic pricing (claude-api skill).
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_DEFAULT_PRICE = (5.0, 25.0)  # Opus-tier fallback for unknown model ids


def price_for(model: str) -> tuple[float, float]:
    if model in PRICING:
        return PRICING[model]
    for key, price in PRICING.items():  # tolerate dated/suffixed ids
        if model.startswith(key):
            return price
    return _DEFAULT_PRICE


def cost_usd(input_tokens: int, output_tokens: int, model: str = MODEL) -> float:
    in_price, out_price = price_for(model)
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Bound each call so a slow/stalled request can't hang a whole
        # preview/generate batch (default SDK timeout is ~10 min).
        _client = anthropic.Anthropic(timeout=120.0, max_retries=2)
    return _client


# ---------- prompt construction --------------------------------------------

def _format_answer_for_prompt(q_meta: QuestionMeta, answer_entry: dict) -> str:
    answers = answer_entry.get("answers", []) or []
    if not answers:
        return "(no answer)"
    values = [a.get("answer") or a.get("value") or "" for a in answers]
    values = [v for v in values if v]
    return ", ".join(values) if values else "(blank)"


def _render_known_answers(survey_id: str, respondent: dict, exclude_qids: set[int]) -> str:
    """Render the respondent's known answers as 'Q (label): text\n  -> answer' lines."""
    questions = get_survey_questions(survey_id)
    answered = respondent_answers_by_qid(respondent)
    lines: list[str] = []
    for q in questions:
        if q.sqlQuestionId in exclude_qids:
            continue
        entry = answered.get(q.sqlQuestionId)
        if not entry:
            continue
        q_text = q.text or q.label or f"Q{q.sqlQuestionId}"
        lines.append(f"Q ({q.label or '-'}): {q_text}\n  -> {_format_answer_for_prompt(q, entry)}")
    return "\n".join(lines) if lines else "(none)"


# ---------- ad-hoc questions ("ask your own" + AI-answer) ------------------

VALID_AD_HOC_TYPES = {"multipleChoice", "checkBoxes", "shortAnswer", "numericAnswer"}
# Types the AI is allowed to PROPOSE in "Generate with AI" — numericAnswer is excluded
# on purpose (we don't want the model inventing number-entry questions).
SUGGEST_TYPES = {"multipleChoice", "checkBoxes", "shortAnswer"}


def _format_custom_question_for_prompt(q: dict) -> str:
    lines = [f"id={q['id']}", f"- type: {q['type']}", f"  text: {q['text']}"]
    opts = q.get("options") or []
    if opts:
        joined = ", ".join(f"\"{o}\"" for o in opts)
        lines.append(f"  options: [{joined}]")
    return "\n".join(lines)


def build_ad_hoc_prompt(survey_id: str, respondent: dict, custom_questions: list[dict]) -> str:
    known_block = _render_known_answers(survey_id, respondent, set())
    questions_block = "\n\n".join(_format_custom_question_for_prompt(q) for q in custom_questions)
    return f"""You are simulating one specific survey respondent. Below are their known answers to a real survey. Answer each NEW question below as this SAME respondent would, staying consistent with everything they've already said (age, gender, region, brand preferences, income, behaviour, etc.).

Each KNOWN ANSWER line below is in the form:
  Q (Label): question text
    -> respondent's answer

The label in parentheses (e.g. "Age", "Gender", "Region") is what you MUST cite when grounding your answer.

KNOWN ANSWERS:
{known_block}

NEW QUESTIONS TO ANSWER:
{questions_block}

Rules:
- For multipleChoice: pick exactly ONE option from the listed options (use the option's label string verbatim).
- For checkBoxes: pick one or more options, returned as a list of label strings.
- For shortAnswer (open-ended): answer in EGYPTIAN colloquial Arabic (اللهجة المصرية العامية, "bel masry") — a short, natural free-text answer (one phrase or sentence) exactly as a real Egyptian respondent would type it on their phone. Do NOT answer in English or in formal/standard Arabic.
- For numericAnswer: a single number as a string.
- GROUND every open-ended (shortAnswer) answer in the KNOWN ANSWERS above — that survey is the ONLY record of what this respondent has actually said, and it is your source of truth. Do NOT invent experiences, incidents, problems, or facts the survey doesn't support.
- Distinguish what the respondent VALUES from what they EXPERIENCED. Rating something as important or as a top driver (e.g. "on time delivery", "delivery speed") only shows a preference — it does NOT mean they ever suffered a problem with it. Never turn a preference or importance rating into a claimed bad experience.
- If an open-ended question asks about a problem, complaint, or bad experience and NOTHING in the KNOWN ANSWERS points to one, it is correct and expected to answer that they faced no real problem (e.g. "مواجهتش مشكلة كبيرة" / "كله كان تمام معايا") instead of fabricating an issue.
- Be decisive about the respondent's stated opinions and preferences, but do not over-claim: only assert facts, events, or experiences that the KNOWN ANSWERS actually support.
- "references": MUST be a list of {{"label": <known-answer label>, "answer": <that answer verbatim>}} for every known answer you actually used to decide. Cite ONLY labels that appear in KNOWN ANSWERS above; do not invent. If the answer is grounded in the ABSENCE of any reported problem, references may be an empty list.
- "reason": one short sentence explaining the inference, referring to the cited answers (or noting that the survey shows no such problem).

Return ONLY a JSON object, no prose:
{{
  "answers": [
    {{
      "id": <int>,
      "answer": <string | list of strings>,
      "reason": "<one short sentence>",
      "references": [{{"label": "Age", "answer": "30-38"}}]
    }}
  ]
}}"""


def _existing_questions_block(survey_id: str) -> tuple[str, str]:
    """Returns (questions_summary, survey_name) for the suggest prompt."""
    questions = get_survey_questions(survey_id)
    lines = []
    for q in questions:
        opts = ""
        if q.options:
            labels = [o["label"] for o in q.options if o.get("label")]
            if labels:
                opts = " — options: " + ", ".join(labels[:8]) + ("…" if len(labels) > 8 else "")
        text = q.text or q.label or f"Q{q.sqlQuestionId}"
        lines.append(f"- [{q.label or '-'}] {text} ({q.type}){opts}")
    return "\n".join(lines), ""


def suggest_questions(survey_id: str, n: int = 5, already: list[str] | None = None,
                      focus: str | None = None) -> dict:
    """Ask Claude to propose `n` novel questions a survey owner might have missed.

    `already` holds question texts already in the builder (including ones suggested on
    earlier "Generate with AI" clicks). We feed them to Claude so it avoids re-proposing
    or lightly rewording them, and dedupe defensively against them afterwards.
    `focus` optionally steers all suggestions toward a researcher-given theme.
    """
    from services.data import get_survey  # local import to avoid cycle at module load
    survey = get_survey(survey_id)
    survey_name = survey.get("name", "")
    existing_block, _ = _existing_questions_block(survey_id)

    already = [a.strip() for a in (already or []) if a and a.strip()]
    already_block = ""
    if already:
        already_lines = "\n".join(f"- {a}" for a in already)
        already_block = f"""

ALREADY DRAFTED (these are in the survey owner's builder right now, including questions you proposed on earlier clicks). Do NOT repeat or lightly reword ANY of these — propose genuinely different ones:
{already_lines}"""

    focus_block = ""
    if focus and focus.strip():
        focus_block = f"""

The survey owner specifically wants questions about: {focus.strip()}. Every proposed question MUST address this focus (while still not duplicating anything existing or already drafted)."""

    prompt = f"""You are reviewing a real consumer-research survey called "{survey_name}". The full list of existing questions is below.

Propose {n} NEW questions the survey owner might have forgotten to include — questions that would deepen the insight without duplicating anything already asked or already drafted. They should be relevant to the survey's theme (brand awareness, usage, attitudes, intent, demographics, etc.) but cover angles missing from the current set.{focus_block}

EXISTING QUESTIONS:
{existing_block}{already_block}

Rules:
- Each new question MUST be genuinely different in topic or angle from every existing question AND from everything under ALREADY DRAFTED (if present).
- `type` MUST be one of: "multipleChoice", "checkBoxes", "shortAnswer". Do NOT propose "numericAnswer" or any question that asks the respondent to type a number — convert such ideas into multipleChoice ranges (e.g. age bands, frequency buckets) instead.
- Use a MIX of types, and at least ONE question MUST be of type "shortAnswer" — an open-ended, free-text question that invites the respondent to answer in their own words (e.g. an opinion, reason, or suggestion).
- For multipleChoice / checkBoxes: provide 3–6 mutually exclusive short option strings.
- For shortAnswer: omit options.
- Keep question text in English, clear and natural (under 25 words).
- `rationale` is one short sentence explaining why this question is worth adding.

Return ONLY a JSON object, no prose:
{{
  "questions": [
    {{"text": "...", "type": "...", "options": ["...", "..."], "rationale": "..."}}
  ]
}}"""

    msg = get_client().messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    parsed = _parse_json_block(raw)
    suggestions = parsed.get("questions", []) if isinstance(parsed, dict) else []

    seen = {a.lower() for a in already}  # drop anything matching an already-drafted question
    norm = []
    for s in suggestions:
        text = (s.get("text") or "").strip()
        qtype = s.get("type")
        if not text or qtype not in SUGGEST_TYPES:  # excludes numericAnswer
            continue
        if text.lower() in seen:
            continue
        options = [str(o).strip() for o in (s.get("options") or []) if str(o).strip()]
        if qtype in ("multipleChoice", "checkBoxes") and len(options) < 2:
            continue
        norm.append({
            "text": text,
            "type": qtype,
            "options": options,
            "rationale": s.get("rationale", ""),
        })
        seen.add(text.lower())
    return {"model": MODEL, "questions": norm}


def suggest_segmentation_axes(survey_name: str, labels: list[dict]) -> dict:
    """Ask Claude which 3-6 question labels to build audience segments around.

    `labels` is [{"label", "type"?, "question_text"?}]. Returns
    {"model", "approach", "axes": [{"label", "reason"}]}. Only labels present in
    the input survive (no invented columns), so the result maps to real data.
    """
    valid = {l["label"] for l in labels if l.get("label")}
    catalog_lines = []
    for l in labels:
        lbl = l.get("label")
        if not lbl:
            continue
        t = f" [{l['type']}]" if l.get("type") else ""
        qt = f": {l['question_text']}" if l.get("question_text") else ""
        catalog_lines.append(f"- {lbl}{t}{qt}")
    catalog = "\n".join(catalog_lines)

    prompt = f"""You are an audience-segmentation analyst planning how to segment a consumer-research survey called "{survey_name}".

Pick the 3 to 6 question labels that would produce the most distinct, actionable audience personas. Favor demographics, behaviors, media/platform usage, and attitudes. Avoid identifiers, purely free-text columns, and near-duplicate labels.

AVAILABLE LABELS (column [type]: question text):
{catalog}

Return ONLY a JSON object, no prose:
{{
  "approach": "1-2 plain sentences on the overall segmentation logic",
  "axes": [
    {{"label": "<EXACT label copied from the list>", "reason": "one short sentence why it matters"}}
  ]
}}

Rules:
- Every "label" MUST be copied EXACTLY from AVAILABLE LABELS. Do not invent labels.
- Choose between 3 and 6 labels.
- Do not use em dashes anywhere in your text; use commas or periods."""

    msg = get_client().messages.create(
        model=MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    parsed = _parse_json_block(raw)
    axes_in = parsed.get("axes", []) if isinstance(parsed, dict) else []

    axes, seen = [], set()
    for a in axes_in:
        lbl = (a.get("label") or "").strip()
        if lbl in valid and lbl not in seen:
            axes.append({"label": lbl, "reason": (a.get("reason") or "").strip()})
            seen.add(lbl)
    approach = (parsed.get("approach") or "").strip() if isinstance(parsed, dict) else ""
    return {"model": MODEL, "approach": approach, "axes": axes}


def ask_ad_hoc(survey_id: str, respondent: dict, custom_questions: list[dict]) -> dict:
    prompt = build_ad_hoc_prompt(survey_id, respondent, custom_questions)

    msg = get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS * 2,  # references bloat the response
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    parsed = _parse_json_block(raw)
    answers = parsed.get("answers", []) if isinstance(parsed, dict) else []

    usage = getattr(msg, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)

    # Build a lookup of valid labels -> actual answer text, so we can drop hallucinated refs.
    questions = get_survey_questions(survey_id)
    answered = respondent_answers_by_qid(respondent)
    valid_refs: dict[str, str] = {}
    for q in questions:
        entry = answered.get(q.sqlQuestionId)
        if entry and q.label:
            valid_refs[q.label] = _format_answer_for_prompt(q, entry)

    by_id = {q["id"]: q for q in custom_questions}
    norm = []
    for a in answers:
        qid = a.get("id")
        ans = a.get("answer")
        if isinstance(ans, str):
            ans_list = [ans]
        elif isinstance(ans, list):
            ans_list = [str(x) for x in ans]
        else:
            ans_list = [str(ans)] if ans is not None else []

        refs_clean = []
        for r in a.get("references", []) or []:
            if not isinstance(r, dict):
                continue
            label = r.get("label") or r.get("question")
            if label and label in valid_refs:
                refs_clean.append({"label": label, "answer": valid_refs[label]})

        src = by_id.get(qid, {})
        norm.append({
            "id": qid,
            "type": src.get("type"),
            "text": src.get("text"),
            "answer": ans_list,
            "reason": a.get("reason", ""),
            "references": refs_clean,
        })
    return {
        "model": MODEL,
        "raw": raw,
        "answers": norm,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost_usd(in_tok, out_tok, MODEL),
        },
    }


# ---------- JSON parsing ---------------------------------------------------

def _parse_json_block(text: str) -> Any:
    """Lenient JSON extraction: handles fenced blocks or trailing/leading prose."""
    text = text.strip()
    if text.startswith("```"):
        # strip fenced block
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # find first '{' and last '}'
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            return {}
    return {}
