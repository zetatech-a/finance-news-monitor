from __future__ import annotations

from dataclasses import dataclass, field
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
    relevance_score: int | None = None
    relevance_prob: float | None = None
    relevance_label: str | None = None
    normalized_title: str | None = None
    cluster_key: str | None = None
    cluster_id: str | None = None
    cluster_size: int | None = None
    cluster_rank: int | None = None
    cluster_is_representative: bool | None = None
    related_count: int | None = None
    related_articles: list[dict[str, str]] = field(default_factory=list)
    # dedup 단계에서 같은 제목으로 흡수된 다른 출처 기사들의 최소 메타.
    # issue_cluster가 최종 cluster_size와 related_articles에 병합한다.
    duplicate_sources: list[dict[str, str]] = field(default_factory=list)


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
