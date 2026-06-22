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


def _render_known_answers(survey_id: str, respondent: dict, exclude_qids: set[int],
                          include_qids: set[int] | None = None) -> str:
    """Render the respondent's known answers as 'Q (label): text\n  -> answer' lines.

    `exclude_qids` drops specific questions. `include_qids`, when given, keeps ONLY those
    questions (used by the backtest path to feed just the seed answers as the persona).
    """
    questions = get_survey_questions(survey_id)
    answered = respondent_answers_by_qid(respondent)
    lines: list[str] = []
    for q in questions:
        if q.sqlQuestionId in exclude_qids:
            continue
        if include_qids is not None and q.sqlQuestionId not in include_qids:
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

# Money / personal-finance topics are banned from suggestions. The prompt already forbids
# them; this is a defensive net that drops any that slip through. Word-boundary anchored to
# avoid false positives (e.g. "spending time" is not matched — only money-sense terms are).
import re as _re
_MONEY_RX = _re.compile(
    r"\b(salar(?:y|ies)|income|wages?|earnings?|earn|money|afford\w*|budget|"
    r"prices?|pricing|cost\w*|expensive|cheap|willing\s+to\s+pay)\b",
    _re.IGNORECASE,
)


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
- LOOK WIDE across the WHOLE profile: before deciding, scan EVERY one of the respondent's KNOWN ANSWERS, not just the one or two most obvious. Pull together every answer that genuinely bears on the question — the most direct signals first, plus related behaviours, attitudes, category/brand usage, and demographics that meaningfully support or qualify the choice. When several answers point the same way, use and cite ALL of them so the answer rests on the widest real evidence available. (This means breadth of GENUINE evidence — still exclude answers with no real bearing, and do not pad with proxies or stereotypes; see REASON LOGICALLY below.)
- GROUND every open-ended (shortAnswer) answer in the KNOWN ANSWERS above — that survey is the ONLY record of what this respondent has actually said, and it is your source of truth. Do NOT invent experiences, incidents, problems, or facts the survey doesn't support.
- Distinguish what the respondent VALUES from what they EXPERIENCED. Rating something as important or as a top driver (e.g. "on time delivery", "delivery speed") only shows a preference — it does NOT mean they ever suffered a problem with it. Never turn a preference or importance rating into a claimed bad experience.
- If an open-ended question asks about a problem, complaint, or bad experience and NOTHING in the KNOWN ANSWERS points to one, it is correct and expected to answer that they faced no real problem (e.g. "مواجهتش مشكلة كبيرة" / "كله كان تمام معايا") instead of fabricating an issue.
- Be decisive about the respondent's stated opinions and preferences, but do not over-claim: only assert facts, events, or experiences that the KNOWN ANSWERS actually support.
- REASON LOGICALLY: the chosen answer must follow sensibly from the answers you cite. Do NOT lean on weak proxies or demographic stereotypes — e.g. do not infer someone's interest, desire, or attitude purely from their socio-economic class, income, age, or occupation; those indicate means or circumstance, not preference. Use such fields only when they are genuinely, directly relevant. When the KNOWN ANSWERS contain no signal that actually bears on the question, pick the most plausible neutral option and say so, rather than inventing a rationale.
- The "reason" MUST justify the EXACT option you picked and point in the same direction as it. Never write a reason that argues for a different option than the one you chose (e.g. do not say "limited interest" while selecting a positive option like "Slightly interested"; if the cited answers point to limited interest, pick the lowest-interest option instead). The reason and the answer must agree.
- "references": MUST list {{"label": <known-answer label>, "answer": <that answer verbatim>}} for EVERY known answer that genuinely supports your choice — aim for the FULL set of relevant supporting evidence, not just one or two. Cite ONLY labels that appear in KNOWN ANSWERS above; do not invent, and do not list answers that have no real bearing just to look thorough. If the answer is grounded in the ABSENCE of any reported problem, references may be an empty list.
- "reason": 2 to 4 sentences that actually show your inference, not a one-line summary. State which cited answers you used, how you weighed them, and why they point to the option you chose. If a signal pushes the other way, say how you resolved it. When the KNOWN ANSWERS contain NO answer that directly measures what the question asks (e.g. the question asks frequency/amount but the survey never asked it), say so explicitly and explain that the choice is a soft inference from proxies — do not present a guess as if it were certain.
- OUTPUT ORDER: fill the fields in the exact order shown below — first list the "references" you are relying on, then write the "reason" from them, and ONLY THEN pick the "answer" that follows from that reasoning. Reason first, decide second; never write the answer before the reason.

Return ONLY a JSON object, no prose. Within each item keep the field order below (reason before answer):
{{
  "answers": [
    {{
      "id": <int>,
      "references": [{{"label": "Age", "answer": "30-38"}}],
      "reason": "<2-4 sentences showing the inference; note explicitly if no direct signal exists>",
      "answer": <string | list of strings>
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
    # The exact set of labels a suggestion is allowed to claim it can be inferred from.
    # Mirrors the reference-validation done in ask_ad_hoc so the model can't ground a
    # suggestion in a column that doesn't exist.
    valid_labels = {q.label for q in get_survey_questions(survey_id) if q.label}

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

Propose {n} NEW questions the survey owner might have forgotten to include. Each should add a genuinely different angle from what's already asked. Lean toward questions whose likely answer can be inferred from the respondent's existing answers (no far-fetched assumptions or invented facts), but you don't have to stay strictly inside the existing data: it's fine for a few to open up fresh angles that extend a bit beyond what the survey already captures, as long as they stay relevant to its theme.{focus_block}

EXISTING QUESTIONS (each line is "- [Label] question text (type)"):
{existing_block}{already_block}

Rules:
- Each new question MUST be genuinely different in topic or angle from every existing question AND from everything under ALREADY DRAFTED (if present).
- GROUNDING: in "grounding", list the EXACT [Label] values of any existing questions whose answers would help infer this respondent's answer. Prefer questions you can ground in at least one existing label, but a minority of fresh-angle questions with little or no grounding are acceptable (leave their "grounding" empty) — just don't make the whole batch ungrounded.
- TOPIC EXCLUSION: NEVER propose questions about money, salary, income, wages, earnings, household budget, spending, prices, affordability, cost, or willingness to pay. Avoid personal-finance topics entirely.
- `type` MUST be one of: "multipleChoice", "checkBoxes", "shortAnswer". Do NOT propose "numericAnswer" or any question that asks the respondent to type a number — convert such ideas into multipleChoice ranges (e.g. age bands, frequency buckets) instead.
- Provide a MIX of all three types. When proposing 3 or more questions, include AT LEAST ONE "multipleChoice" (single answer), AT LEAST ONE "checkBoxes" (multiple answers / pick many), and AT LEAST ONE "shortAnswer" (open-ended free text). Never return a batch that is all one type.
- For multipleChoice / checkBoxes: provide 3–6 mutually exclusive short option strings.
- For shortAnswer: omit options.
- Keep question text in English, clear and natural (under 25 words).
- `rationale` is one short sentence explaining why this question is worth adding.

Return ONLY a JSON object, no prose:
{{
  "questions": [
    {{"text": "...", "type": "...", "options": ["...", "..."], "grounding": ["Label1", "Label2"], "rationale": "..."}}
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
        # Defensive net: drop any money/finance question that slipped past the prompt ban.
        if _MONEY_RX.search(text) or any(_MONEY_RX.search(o) for o in options):
            continue
        if qtype in ("multipleChoice", "checkBoxes") and len(options) < 2:
            continue
        # Keep only grounding labels that actually exist in this survey (drop hallucinated
        # ones), deduped and order-preserving. An empty list means "weakly grounded" — the
        # UI flags it rather than dropping the question (soft steer, not a hard filter).
        grounding, gseen = [], set()
        for g in (s.get("grounding") or []):
            lbl = str(g).strip()
            if lbl in valid_labels and lbl not in gseen:
                grounding.append(lbl)
                gseen.add(lbl)
        norm.append({
            "text": text,
            "type": qtype,
            "options": options,
            "grounding": grounding,
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
- Write "approach" and each "reason" in light Markdown: wrap the 1-2 most important
  terms in **bold**. "approach" may use a short "- " bullet list if it sharpens the logic.
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

    # Scale the budget with question count: each answer now carries a 2-4 sentence reason
    # plus references, so a fixed cap would truncate the JSON on multi-question requests.
    n = max(1, len(custom_questions))
    max_toks = max(3000, min(16000, 450 * n + 1500))
    msg = get_client().messages.create(
        model=MODEL,
        max_tokens=max_toks,
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


# ---------- backtest (predict held-out answers from a seed persona) --------

def build_backtest_prompt(survey_id: str, respondent: dict, seed_qids: set[int],
                          holdout_questions: list[dict]) -> str:
    """Like build_ad_hoc_prompt, but the KNOWN ANSWERS block is limited to the seed
    questions and the task is to PREDICT this respondent's real answers to the held-out
    questions. The held-out answers are deliberately NOT shown (no leakage)."""
    known_block = _render_known_answers(survey_id, respondent, set(), include_qids=seed_qids)
    questions_block = "\n\n".join(_format_custom_question_for_prompt(q) for q in holdout_questions)
    return f"""You are simulating one specific survey respondent. Below is a SUBSET of their real answers to a survey (their "persona"). Based ONLY on this subset, PREDICT how this SAME respondent answered each of the OTHER questions below, staying consistent with everything in the subset (age, gender, region, brand preferences, income, behaviour, etc.).

Each KNOWN ANSWER line below is in the form:
  Q (Label): question text
    -> respondent's answer

The label in parentheses (e.g. "Age", "Gender", "Region") is what you MUST cite when grounding your answer.

KNOWN ANSWERS (the persona — this is all you know about this respondent):
{known_block}

QUESTIONS TO PREDICT:
{questions_block}

Rules:
- For multipleChoice: pick exactly ONE option from the listed options (use the option's label string verbatim).
- For checkBoxes: pick one or more options, returned as a list of label strings.
- For shortAnswer (open-ended): answer in EGYPTIAN colloquial Arabic (اللهجة المصرية العامية, "bel masry") — a short, natural free-text answer (one phrase or sentence) exactly as a real Egyptian respondent would type it on their phone. Do NOT answer in English or in formal/standard Arabic.
- For numericAnswer: a single number as a string.
- Predict the MOST LIKELY answer this respondent gave, inferring from the KNOWN ANSWERS above. Be decisive — pick the single best option rather than hedging.
- LOOK WIDE across the WHOLE persona: before predicting, scan EVERY one of the KNOWN ANSWERS, not just the one or two most obvious. Pull together every answer that genuinely bears on the question — the most direct signals first, plus related behaviours, attitudes, and usage that support or qualify the choice. When several answers point the same way, use and cite ALL of them. (Breadth of GENUINE evidence only — exclude answers with no real bearing and do not pad with proxies or stereotypes.)
- Do NOT invent facts beyond what the KNOWN ANSWERS reasonably imply.
- REASON LOGICALLY: the prediction must follow sensibly from the answers you cite. Do NOT lean on weak proxies or demographic stereotypes — e.g. do not infer interest, desire, or attitude purely from socio-economic class, income, age, or occupation; those indicate means or circumstance, not preference. Use such fields only when genuinely, directly relevant.
- "references": MUST list {{"label": <known-answer label>, "answer": <that answer verbatim>}} for EVERY known answer that genuinely supports your prediction — aim for the FULL set of relevant supporting evidence, not just one or two. Cite ONLY labels that appear in KNOWN ANSWERS above; do not invent, and do not list answers with no real bearing just to look thorough.
- "reason": 2 to 4 sentences that actually show your inference, not a one-line summary. State which cited answers you used, how you weighed them, and why they point to the option you chose. If a signal pushes the other way, say how you resolved it. When the KNOWN ANSWERS contain NO answer that directly measures what the question asks, say so explicitly and explain that the prediction is a soft inference from proxies.
- OUTPUT ORDER: fill the fields in the exact order shown below — first list the "references" you are relying on, then write the "reason" from them, and ONLY THEN pick the "answer" that follows from that reasoning. Reason first, decide second; never write the answer before the reason.

Return ONLY a JSON object, no prose. Within each item keep the field order below (reason before answer):
{{
  "answers": [
    {{
      "id": <int>,
      "references": [{{"label": "Age", "answer": "30-38"}}],
      "reason": "<2-4 sentences showing the inference; note explicitly if no direct signal exists>",
      "answer": <string | list of strings>
    }}
  ]
}}"""


def ask_backtest(survey_id: str, respondent: dict, seed_qids: set[int],
                 holdout_questions: list[dict]) -> dict:
    """Predict a respondent's held-out answers from their seed answers only.

    Mirrors ask_ad_hoc but (a) uses the leak-free backtest prompt and (b) restricts the
    valid reference set to the SEED answers, so the model can't 'cite' a held-out answer.
    """
    prompt = build_backtest_prompt(survey_id, respondent, seed_qids, holdout_questions)

    # Scale the output budget with the number of held-out questions: each one needs an
    # answer + reason + references, so a fixed cap silently truncates the JSON on large
    # surveys (the model returns 200 with stop_reason=max_tokens and unparseable output).
    n = max(1, len(holdout_questions))
    max_toks = max(3000, min(16000, 320 * n + 1500))
    msg = get_client().messages.create(
        model=MODEL,
        max_tokens=max_toks,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
    parsed = _parse_json_block(raw)
    answers = parsed.get("answers", []) if isinstance(parsed, dict) else []

    # Surface truncation instead of silently predicting nothing.
    if not answers and getattr(msg, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            f"model output hit the {max_toks}-token limit before returning JSON "
            f"({n} held-out questions) — exclude some questions and retry")

    usage = getattr(msg, "usage", None)
    in_tok = int(getattr(usage, "input_tokens", 0) or 0)
    out_tok = int(getattr(usage, "output_tokens", 0) or 0)

    # Valid refs come ONLY from the seed answers — never from held-out questions.
    questions = get_survey_questions(survey_id)
    answered = respondent_answers_by_qid(respondent)
    valid_refs: dict[str, str] = {}
    for q in questions:
        if q.sqlQuestionId not in seed_qids:
            continue
        entry = answered.get(q.sqlQuestionId)
        if entry and q.label:
            valid_refs[q.label] = _format_answer_for_prompt(q, entry)

    by_id = {q["id"]: q for q in holdout_questions}
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
