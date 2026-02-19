from __future__ import annotations

from typing import Iterable

from src.pipeline.normalize import Article


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        canonical_link = (
            (article.naver_link or "").strip()
            or (article.originallink or "").strip()
            or (article.link or "").strip()
        )
        key = f"{(article.title or '').lower()}|{canonical_link}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique
