from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    title: str
    description: str
    link: str  # 클릭용(원문 우선)
    originallink: str | None
    naver_link: str | None  # 본문 추출용(있으면 우선)
    pub_date: datetime
    query: str


def normalize(raw_items: list[dict]) -> list[Article]:
    articles: list[Article] = []
    for item in raw_items:
        if not item.get("title") or not item.get("link"):
            continue
        articles.append(
            Article(
                title=(item.get("title") or "").strip(),
                description=(item.get("description") or "").strip(),
                link=(item.get("link") or "").strip(),
                originallink=item.get("originallink"),
                naver_link=item.get("naver_link"),
                pub_date=item.get("pubDate"),
                query=item.get("query", ""),
            )
        )
    return articles
