from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.ml.relevance_model import load_model, predict_proba
from src.pipeline.relevance_score import relevance_score


def _get(article: Any, key: str) -> str:
    if isinstance(article, dict):
        return (article.get(key) or "").strip()
    return (getattr(article, key, "") or "").strip()

def _text(article: Any) -> str:
    title = _get(article, "title")
    summary = _get(article, "summary") or _get(article, "description")
    return f"{title}\n{summary}".strip()

def _set_article_meta(article: Any, *, score: int, prob: float | None) -> None:
    label = "high" if score >= 8 else "med" if score >= 4 else "low"
    if isinstance(article, dict):
        article["relevance_score"] = score
        article["score"] = score
        article["relevance_prob"] = prob
        article["prob"] = prob
        article["relevance_label"] = label
        article["relevance_bucket"] = label
        return

    setattr(article, "relevance_score", score)
    setattr(article, "score", score)
    setattr(article, "relevance_prob", prob)
    setattr(article, "prob", prob)
    setattr(article, "relevance_label", label)
    setattr(article, "relevance_bucket", label)


def filter_relevance(
    articles: list[Any],
    model_path: Path,
    out_candidates_csv: Path | None = None,
    min_prob: float = 0.60,
    min_score: int = 2,
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
        keep = (p is not None and p >= min_prob) or (p is None and s >= min_score) or (p is not None and s >= min_score)

        _set_article_meta(a, score=s, prob=p)

        if keep:
            kept.append(a)

        if out_candidates_csv:
            rows.append({
                "title": _get(a, "title"),
                "summary": _get(a, "summary") or _get(a, "description"),
                "url": _get(a, "url") or _get(a, "link"),
                "score": s,
                "prob": "" if p is None else round(p, 4),
                "keep": int(keep),
            })

    if out_candidates_csv:
        out_candidates_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_candidates_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["title", "summary", "url", "score", "prob", "keep"])
            w.writeheader()
            w.writerows(rows)

    return kept
