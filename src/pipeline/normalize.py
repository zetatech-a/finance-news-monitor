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
    # --- 관련성 필터(relevance_filter)가 채우는 필드 ---
    relevance_score: int | None = None
    relevance_prob: float | None = None
    relevance_label: str | None = None
    decision: str | None = None  # "keep" | "drop"
    decision_reason: str | None = None
    keep: bool | None = None
    relevance_model_policy: str | None = None
    model_used: bool | None = None
    candidate_keep_prob: float | None = None
    candidate_drop_prob: float | None = None
    matched_hard: str | None = None  # ";"로 연결된 매칭 용어들
    matched_soft: str | None = None
    matched_negative: str | None = None
    # --- 요약 단계(run_daily)가 채우는 필드 ---
    summary_cached: bool | None = None  # UI에서 ⚡ 캐시 배지 표시용
    # Gemini 3줄 요약은 **표시 전용**이다. description(관련성/태깅/클러스터링/랭킹 입력)은
    # 절대 덮어쓰지 않으므로, AI 요약이 기존 분류 결과를 바꾸지 않는다.
    summary_lines: list[str] = field(default_factory=list)
    summary_source: str | None = None  # "gemini" | None(추출요약/네이버 스니펫)
    # --- dedup / issue_cluster가 채우는 필드 ---
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
