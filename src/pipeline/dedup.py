from __future__ import annotations

from typing import Iterable

from src.pipeline.normalize import Article


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = f"{article.title.lower()}|{article.link}".strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique
