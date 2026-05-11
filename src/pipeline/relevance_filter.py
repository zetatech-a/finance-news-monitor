from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.ml.relevance_model import load_model, predict_proba
from src.pipeline.relevance_score import _WEIGHTS, relevance_score


def _get(article: Any, key: str) -> str:
    if isinstance(article, dict):
        return (article.get(key) or "").strip()
    return (getattr(article, key, "") or "").strip()


def _text(article: Any) -> str:
    title = _get(article, "title")
    summary = _get(article, "summary") or _get(article, "description")
    return f"{title}\n{summary}".strip()


def _matched_terms(article: Any) -> dict[str, str]:
    text = _text(article).lower()

    def find_terms(keywords: dict[str, int]) -> str:
        return ";".join(k for k in keywords if k.lower() in text)

    return {
        "matched_hard": find_terms(_WEIGHTS.hard),
        "matched_soft": find_terms(_WEIGHTS.soft),
        "matched_negative": find_terms(_WEIGHTS.neg),
    }


def _decide_relevance(
    *,
    score: int,
    prob: float | None,
    min_prob: float,
    min_score: int,
    matched_hard: str = "",
    matched_negative: str = "",
) -> tuple[bool, str]:
    if prob is not None:
        if prob >= min_prob:
            return True, "model_keep_prob_ge_threshold"
        return False, "model_drop_prob_lt_threshold"

    if score >= min_score:
        return True, "rule_keep_score_ge_threshold"
    if matched_negative:
        return False, "rule_drop_negative_signal"
    if not matched_hard:
        return False, "rule_drop_no_financial_anchor"
    return False, "rule_drop_score_lt_threshold"


def _set_article_meta(
    article: Any,
    *,
    score: int,
    prob: float | None,
    keep: bool,
    decision_reason: str,
    matched: dict[str, str],
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
        **matched,
    }
    if isinstance(article, dict):
        article.update(values)
        return

    for key, value in values.items():
        setattr(article, key, value)


def filter_relevance(
    articles: list[Any],
    model_path: Path,
    out_candidates_csv: Path | None = None,
    min_prob: float = 0.60,
    min_score: int = 4,
) -> list[Any]:
    model = load_model(model_path)
    texts = [_text(a) for a in articles]
    scores = [relevance_score(a) for a in articles]

    probs = None
    if model is not None:
        probs = predict_proba(model, texts)

    kept: list[Any] = []
    rows = []

    for i, a in enumerate(articles):
        p = probs[i] if probs is not None else None
        s = scores[i]
        matched = _matched_terms(a)
        keep, decision_reason = _decide_relevance(
            score=s,
            prob=p,
            min_prob=min_prob,
            min_score=min_score,
            matched_hard=matched["matched_hard"],
            matched_negative=matched["matched_negative"],
        )

        _set_article_meta(
            a,
            score=s,
            prob=p,
            keep=keep,
            decision_reason=decision_reason,
            matched=matched,
        )

        if keep:
            kept.append(a)

        if out_candidates_csv:
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
                **matched,
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
        ]
        with out_candidates_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    return kept
