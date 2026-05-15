from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from src.ml.relevance_model import load_model, predict_proba
from src.pipeline.relevance_score import matched_terms as score_matched_terms, relevance_score

logger = logging.getLogger(__name__)

ModelPolicy = Literal["authoritative", "candidate_hybrid", "rule_only"]


def _get(article: Any, key: str) -> str:
    if isinstance(article, dict):
        return (article.get(key) or "").strip()
    return (getattr(article, key, "") or "").strip()


def _text(article: Any) -> str:
    title = _get(article, "title")
    summary = _get(article, "summary") or _get(article, "description")
    return f"{title}\n{summary}".strip()


def _matched_terms(article: Any) -> dict[str, str]:
    matched = score_matched_terms(article)
    return {
        "matched_hard": ";".join(matched["hard"]),
        "matched_soft": ";".join(matched["soft"]),
        "matched_negative": ";".join(matched["negative"]),
    }


def _rule_decide_relevance(
    *,
    score: int,
    min_score: int,
    matched_hard: str = "",
    matched_negative: str = "",
) -> tuple[bool, str]:
    if score >= min_score:
        return True, "rule_keep_score_ge_threshold"
    if matched_negative:
        return False, "rule_drop_negative_signal"
    if not matched_hard:
        return False, "rule_drop_no_financial_anchor"
    return False, "rule_drop_score_lt_threshold"


def _decide_relevance(
    *,
    score: int,
    prob: float | None,
    min_prob: float,
    min_score: int,
    matched_hard: str = "",
    matched_negative: str = "",
    model_policy: ModelPolicy = "authoritative",
    candidate_keep_prob: float = 0.65,
    candidate_drop_prob: float = 0.35,
) -> tuple[bool, str]:
    if model_policy == "rule_only":
        return _rule_decide_relevance(
            score=score,
            min_score=min_score,
            matched_hard=matched_hard,
            matched_negative=matched_negative,
        )

    if model_policy == "candidate_hybrid":
        if matched_negative:
            return False, "candidate_hybrid_drop_negative_signal"
        if prob is not None and score >= 8 and matched_hard:
            return True, "candidate_hybrid_keep_strong_rule_anchor"
        if prob is not None and prob >= candidate_keep_prob:
            return True, "candidate_hybrid_model_keep_prob_ge_threshold"
        if prob is not None and prob <= candidate_drop_prob:
            return False, "candidate_hybrid_model_drop_prob_le_threshold"
        if prob is not None:
            if score >= min_score:
                return True, "candidate_hybrid_gray_keep_rule_score_ge_threshold"
            return False, "candidate_hybrid_gray_drop_rule_score_lt_threshold"
        return _rule_decide_relevance(
            score=score,
            min_score=min_score,
            matched_hard=matched_hard,
            matched_negative=matched_negative,
        )

    if prob is not None:
        if prob >= min_prob:
            return True, "model_keep_prob_ge_threshold"
        return False, "model_drop_prob_lt_threshold"

    return _rule_decide_relevance(
        score=score,
        min_score=min_score,
        matched_hard=matched_hard,
        matched_negative=matched_negative,
    )


def _set_article_meta(
    article: Any,
    *,
    score: int,
    prob: float | None,
    keep: bool,
    decision_reason: str,
    matched: dict[str, str],
    model_policy: ModelPolicy,
    model_used: bool,
    candidate_keep_prob: float,
    candidate_drop_prob: float,
) -> None:
    label = "high" if score >= 8 else "med" if score >= 4 else "low"
    values = {
        "relevance_score": score,
        "score": score,
        "relevance_prob": prob,
        "prob": prob,
        "relevance_label": label,
        "relevance_bucket": label,
        "decision": "keep" if keep else "drop",
        "decision_reason": decision_reason,
        "keep": keep,
        "relevance_model_policy": model_policy,
        "model_used": model_used,
        "candidate_keep_prob": candidate_keep_prob,
        "candidate_drop_prob": candidate_drop_prob,
        **matched,
    }
    if isinstance(article, dict):
        article.update(values)
        return

    for key, value in values.items():
        setattr(article, key, value)


def _write_metrics(
    *,
    metrics_path: Path,
    date: str | None,
    model_policy: ModelPolicy,
    model_path: Path,
    model_used: bool,
    min_score: int,
    min_prob: float,
    candidate_keep_prob: float,
    candidate_drop_prob: float,
    input_count: int,
    kept_count: int,
    rows: list[dict[str, Any]],
) -> None:
    decision_reason_counts = Counter(str(row["decision_reason"]) for row in rows)
    payload = {
        "date": date,
        "model_policy": model_policy,
        "model_path": str(model_path),
        "model_used": model_used,
        "min_score": min_score,
        "min_prob": min_prob,
        "candidate_keep_prob": candidate_keep_prob,
        "candidate_drop_prob": candidate_drop_prob,
        "input_count": input_count,
        "kept_count": kept_count,
        "dropped_count": input_count - kept_count,
        "decision_reason_counts": dict(sorted(decision_reason_counts.items())),
        "model_prob_available_count": sum(1 for row in rows if row["prob"] != ""),
        "model_prob_missing_count": sum(1 for row in rows if row["prob"] == ""),
        "candidate_hybrid_model_keep": decision_reason_counts[
            "candidate_hybrid_model_keep_prob_ge_threshold"
        ],
        "candidate_hybrid_model_drop": decision_reason_counts[
            "candidate_hybrid_model_drop_prob_le_threshold"
        ],
        "candidate_hybrid_gray_rule_keep": decision_reason_counts[
            "candidate_hybrid_gray_keep_rule_score_ge_threshold"
        ],
        "candidate_hybrid_gray_rule_drop": decision_reason_counts[
            "candidate_hybrid_gray_drop_rule_score_lt_threshold"
        ],
        "candidate_hybrid_strong_rule_keep": decision_reason_counts[
            "candidate_hybrid_keep_strong_rule_anchor"
        ],
        "candidate_hybrid_negative_drop": decision_reason_counts[
            "candidate_hybrid_drop_negative_signal"
        ],
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def filter_relevance(
    articles: list[Any],
    model_path: Path,
    out_candidates_csv: Path | None = None,
    min_prob: float = 0.60,
    min_score: int = 4,
    model_policy: ModelPolicy = "authoritative",
    candidate_keep_prob: float = 0.65,
    candidate_drop_prob: float = 0.35,
    metrics_path: Path | None = None,
    metrics_date: str | None = None,
) -> list[Any]:
    if model_policy not in {"authoritative", "candidate_hybrid", "rule_only"}:
        raise ValueError(f"unsupported relevance model_policy: {model_policy}")
    if candidate_drop_prob >= candidate_keep_prob:
        raise ValueError("candidate_drop_prob must be less than candidate_keep_prob")

    model = None
    if model_policy != "rule_only":
        try:
            model = load_model(model_path)
        except Exception as exc:
            logger.warning(
                "Failed to load relevance model at %s; falling back to rules: %s",
                model_path,
                exc,
            )

    texts = [_text(a) for a in articles]
    scores = [relevance_score(a) for a in articles]

    probs = None
    if model is not None and model_policy != "rule_only":
        try:
            probs = predict_proba(model, texts)
        except Exception as exc:
            logger.warning(
                "Failed to score relevance model at %s; falling back to rules: %s",
                model_path,
                exc,
            )
            probs = None

    model_used = probs is not None
    kept: list[Any] = []
    rows = []

    for i, a in enumerate(articles):
        p = probs[i] if probs is not None else None
        s = scores[i]
        matched = _matched_terms(a)
        decision_policy: ModelPolicy = model_policy if p is not None else "rule_only"
        keep, decision_reason = _decide_relevance(
            score=s,
            prob=p,
            min_prob=min_prob,
            min_score=min_score,
            matched_hard=matched["matched_hard"],
            matched_negative=matched["matched_negative"],
            model_policy=decision_policy,
            candidate_keep_prob=candidate_keep_prob,
            candidate_drop_prob=candidate_drop_prob,
        )

        _set_article_meta(
            a,
            score=s,
            prob=p,
            keep=keep,
            decision_reason=decision_reason,
            matched=matched,
            model_policy=model_policy,
            model_used=model_used,
            candidate_keep_prob=candidate_keep_prob,
            candidate_drop_prob=candidate_drop_prob,
        )

        if keep:
            kept.append(a)

        prob_value = "" if p is None else round(p, 4)
        rows.append({
            "title": _get(a, "title"),
            "summary": _get(a, "summary") or _get(a, "description"),
            "url": _get(a, "url") or _get(a, "link"),
            "score": s,
            "prob": prob_value,
            "keep": int(keep),
            "decision": "keep" if keep else "drop",
            "decision_reason": decision_reason,
            "relevance_score": s,
            "relevance_prob": prob_value,
            "matched_hard": matched["matched_hard"],
            "matched_soft": matched["matched_soft"],
            "matched_negative": matched["matched_negative"],
            "relevance_model_policy": model_policy,
            "model_used": int(model_used),
            "candidate_keep_prob": candidate_keep_prob,
            "candidate_drop_prob": candidate_drop_prob,
        })

    if out_candidates_csv:
        out_candidates_csv.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "title",
            "summary",
            "url",
            "score",
            "prob",
            "keep",
            "decision",
            "decision_reason",
            "relevance_score",
            "relevance_prob",
            "matched_hard",
            "matched_soft",
            "matched_negative",
            "relevance_model_policy",
            "model_used",
            "candidate_keep_prob",
            "candidate_drop_prob",
        ]
        with out_candidates_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    if metrics_path:
        try:
            _write_metrics(
                metrics_path=metrics_path,
                date=metrics_date,
                model_policy=model_policy,
                model_path=model_path,
                model_used=model_used,
                min_score=min_score,
                min_prob=min_prob,
                candidate_keep_prob=candidate_keep_prob,
                candidate_drop_prob=candidate_drop_prob,
                input_count=len(articles),
                kept_count=len(kept),
                rows=rows,
            )
        except Exception as exc:
            logger.warning("Failed to write relevance filter metrics to %s: %s", metrics_path, exc)

    return kept
