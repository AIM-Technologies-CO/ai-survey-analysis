"""Transport-agnostic backtest flows: predict a respondent's held-out answers from a
seed subset of their real answers, then score the predictions against the real answers.

Shares the survey-selection / filtering / sampling / job machinery with synth_service.
Raises plain exceptions; each transport maps them itself (see synth_service docstring).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from services import data, predictor, tracking
from services import synth_jobs as jobs
from services import synth_service as synth


def holdout_questions(survey_id: str, seed_qids: set[int],
                      exclude_qids: set[int] | None = None) -> list[dict]:
    """Every answerable survey question NOT in the seed set and NOT explicitly excluded,
    as ad-hoc question dicts ({id, text, type, options[]}). Options are the choice labels.
    `exclude_qids` lets the user drop specific questions from the prediction set."""
    exclude = exclude_qids or set()
    out: list[dict] = []
    for q in data.get_survey_questions(survey_id):
        if q.sqlQuestionId in seed_qids or q.sqlQuestionId in exclude:
            continue
        if q.type not in predictor.VALID_AD_HOC_TYPES:
            continue  # not predictable in the ad-hoc answer format
        options = [(o.get("text") or o.get("label")) for o in (q.options or [])
                   if (o.get("text") or o.get("label"))]
        out.append({
            "id": q.sqlQuestionId,
            "text": q.text or q.label or f"Q{q.sqlQuestionId}",
            "type": q.type,
            "options": options,
        })
    return out


def _validate_seed(survey_id: str, seed_qids: set[int],
                   exclude_qids: set[int] | None = None) -> list[dict]:
    if not seed_qids:
        raise ValueError("pick at least one seed question")
    holdout = holdout_questions(survey_id, seed_qids, exclude_qids)
    if not holdout:
        raise ValueError("no questions left to predict — keep at least one question unselected and not removed")
    return holdout


# ---------- comparison / scoring -------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def option_aliases(meta) -> dict[str, tuple[str, str]]:
    """norm(alias) -> (canon_norm, canon_display) for every label/text of each option.

    A respondent's stored answer and the option string we feed the model can be different
    representations of the SAME choice (one stores the answerLabel, the other the value
    text). Resolving both to a canonical option identity makes scoring representation-proof.
    """
    out: dict[str, tuple[str, str]] = {}
    for o in (meta.options if meta else []) or []:
        label, text = o.get("label"), o.get("text")
        canon_disp = text or label
        if not canon_disp:
            continue
        canon = (_norm(canon_disp), canon_disp)
        for alias in (label, text):
            if alias:
                out[_norm(alias)] = canon
    return out


def compare_answer(qtype: str, predicted: list[str], actual: list[str],
                   aliases: dict[str, tuple[str, str]] | None = None) -> dict:
    """Score a predicted answer against the real one. shortAnswer is free text, so it is
    shown but not binary-scored (scored=False) and excluded from accuracy.

    `aliases` (from option_aliases) canonicalizes choice strings so a label vs value-text
    difference between the prediction and the stored answer doesn't count as a mismatch."""
    def canon(s: str) -> str:
        hit = aliases.get(_norm(s)) if aliases else None
        return hit[0] if hit else _norm(s)

    if qtype == "checkBoxes":
        pset = {canon(x) for x in predicted if _norm(x)}
        aset = {canon(x) for x in actual if _norm(x)}
        union = pset | aset
        overlap = (len(pset & aset) / len(union)) if union else 1.0
        return {"scored": True, "match": pset == aset, "overlap": round(overlap, 3)}
    if qtype in ("multipleChoice", "numericAnswer"):
        return {"scored": True, "match": canon(" ".join(predicted)) == canon(" ".join(actual))}
    # shortAnswer (open-ended) and anything else: display only
    return {"scored": False, "match": None}


def shape_backtest_row(survey_id: str, doc: dict, seed_qids: set[int],
                       holdout_qs: list[dict], generated_answers: list, error: str | None) -> dict:
    """Pair each held-out question's predicted answer with the respondent's real answer and
    score it. Also returns the seed answers (for persona context) and per-respondent accuracy."""
    qmeta = {q.sqlQuestionId: q for q in data.get_survey_questions(survey_id)}
    answered = data.respondent_answers_by_qid(doc)
    pred_by_id = {a.get("id"): a for a in (generated_answers or [])}
    holdout_ids = {q["id"] for q in holdout_qs}

    def real_vals(qid: int) -> list[str]:
        entry = answered.get(qid)
        if not entry:
            return []
        vals = [a.get("answer") or a.get("value") or "" for a in (entry.get("answers") or [])]
        return [v for v in vals if v]

    seed = []
    for qid in seed_qids:
        vals = real_vals(qid)
        if not vals:
            continue
        q = qmeta.get(qid)
        seed.append({
            "label": (q.label if q else None) or f"Q{qid}",
            "text": (q.text if q else None) or f"Q{qid}",
            "section": (q.section if q else "") or "",
            "options": [(o.get("text") or o.get("label")) for o in (q.options if q else [])
                        if (o.get("text") or o.get("label"))],
            "answer": ", ".join(vals),
        })

    comparisons = []
    matched = scored = 0
    for q in holdout_qs:
        qid = q["id"]
        actual = real_vals(qid)
        if not actual:
            continue  # respondent never answered this — no ground truth to compare
        pred = pred_by_id.get(qid, {})
        predicted = pred.get("answer") or []
        meta = qmeta.get(qid)
        aliases = option_aliases(meta)
        cmp = compare_answer(q["type"], predicted, actual, aliases)
        if cmp["scored"]:
            scored += 1
            if cmp["match"]:
                matched += 1
        # Show both columns in the same (canonical) representation so a label-vs-text
        # difference doesn't read as a mismatch to the eye.
        def _disp(s):
            hit = aliases.get(_norm(s))
            return hit[1] if hit else s
        comparisons.append({
            "label": (meta.label if meta else None) or f"Q{qid}",
            "text": q["text"],
            "type": q["type"],
            "section": (meta.section if meta else "") or "",
            "options": q.get("options") or [],
            "predicted": [_disp(p) for p in predicted],
            "actual": ", ".join(_disp(a) for a in actual),
            "scored": cmp["scored"],
            "match": cmp["match"],
            "overlap": cmp.get("overlap"),
            "reason": pred.get("reason", ""),
            "references": pred.get("references", []),
        })

    return {
        "id": str(doc.get("_id", "")),
        "submitDate": data.jsonable(doc.get("submitDate")),
        "seed": seed,
        "comparisons": comparisons,
        "scored": scored,
        "matched": matched,
        "accuracy": (matched / scored) if scored else None,
        "error": error,
    }


# ---------- preview + generate-all -----------------------------------------

def run_backtest_preview(survey_id: str, seed_qids: list[int], *, exclude_qids: list[int] | None = None,
                         date_from: str | None = None,
                         date_to: str | None = None, include_all: bool = False,
                         session_id: str | None = None) -> dict:
    data.get_survey(survey_id)
    seed_set = {int(q) for q in seed_qids}
    exclude_set = {int(q) for q in (exclude_qids or [])}
    holdout = _validate_seed(survey_id, seed_set, exclude_set)
    synth.require_api_key()
    kwargs = synth.build_filter_kwargs(date_from, date_to, include_all)

    eligible = data.count_eligible(survey_id, **kwargs)
    docs = data.sample_eligible(survey_id, jobs.PREVIEW_SAMPLE, **kwargs)

    results: list = [None] * len(docs)
    usages: list = [None] * len(docs)

    def work(i, doc):
        try:
            out = predictor.ask_backtest(survey_id, doc, seed_set, holdout)
            return i, shape_backtest_row(survey_id, doc, seed_set, holdout, out["answers"], None), out.get("usage")
        except Exception as e:
            return i, shape_backtest_row(survey_id, doc, seed_set, holdout, [], f"{type(e).__name__}: {e}"), None

    if docs:
        with ThreadPoolExecutor(max_workers=jobs.PREVIEW_WORKERS) as ex:
            for f in as_completed([ex.submit(work, i, d) for i, d in enumerate(docs)]):
                i, row, usage = f.result()
                results[i] = row
                usages[i] = usage

    scored_usages = [u for u in usages if u]
    total_in = sum(u.get("input_tokens", 0) for u in scored_usages)
    total_out = sum(u.get("output_tokens", 0) for u in scored_usages)
    total_cost = sum(u.get("cost_usd", 0.0) for u in scored_usages)
    per_resp = (total_cost / len(scored_usages)) if scored_usages else 0.0

    # overall accuracy = matched / scored across all previewed respondents
    tot_matched = sum(r["matched"] for r in results if r)
    tot_scored = sum(r["scored"] for r in results if r)
    overall_accuracy = (tot_matched / tot_scored) if tot_scored else None

    tracking.log(session_id, survey_id, "backtest_preview_run",
                 {"sample": len(docs), "eligible": eligible, "seed_count": len(seed_set),
                  "holdout_count": len(holdout), "cost_usd": total_cost,
                  "accuracy": overall_accuracy, "matched": tot_matched, "scored": tot_scored})
    return {
        "model": predictor.MODEL,
        "eligible": eligible,
        "sample": len(docs),
        "cap": jobs.MAX_GENERATE_RESPONDENTS,
        "seed_count": len(seed_set),
        "holdout_count": len(holdout),
        "accuracy": overall_accuracy,
        "matched": tot_matched,
        "scored": tot_scored,
        "cost": {
            "scored": len(scored_usages),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_usd": total_cost,
            "per_respondent_usd": per_resp,
            "projected_full_run_usd": per_resp * min(eligible, jobs.MAX_GENERATE_RESPONDENTS),
        },
        "results": results,
    }


def start_backtest_all(survey_id: str, seed_qids: list[int], *, exclude_qids: list[int] | None = None,
                       date_from: str | None = None,
                       date_to: str | None = None, include_all: bool = False,
                       session_id: str | None = None) -> dict:
    data.get_survey(survey_id)
    seed_set = {int(q) for q in seed_qids}
    exclude_set = {int(q) for q in (exclude_qids or [])}
    holdout = _validate_seed(survey_id, seed_set, exclude_set)
    synth.require_api_key()
    kwargs = synth.build_filter_kwargs(date_from, date_to, include_all)

    eligible = data.count_eligible(survey_id, **kwargs)
    if eligible == 0:
        raise ValueError("no eligible respondents for this filter")

    view = jobs.start_backtest_job(survey_id, seed_set, holdout, kwargs,
                                   eligible=eligible, session_id=session_id)
    tracking.log(session_id, survey_id, "backtest_all_started",
                 {"job_id": view["id"], "total": view["total"], "eligible": eligible,
                  "capped": view["capped"], "seed_count": len(seed_set),
                  "holdout_count": len(holdout)})
    return view
