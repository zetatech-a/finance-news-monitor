from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    title: str
    description: str
    link: str
    originallink: str | None
    pub_date: datetime
    query: str


def normalize(raw_items: list[dict]) -> list[Article]:
    articles: list[Article] = []
    for item in raw_items:
        if not item.get("title") or not item.get("link"):
            continue
        articles.append(
            Article(
                title=item.get("title", "").strip(),
                description=item.get("description", "").strip(),
                link=item.get("link", "").strip(),
                originallink=item.get("originallink"),
                pub_date=item.get("pubDate"),
                query=item.get("query", ""),
            )
        )
    return articles
