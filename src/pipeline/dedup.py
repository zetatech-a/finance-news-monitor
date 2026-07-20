from __future__ import annotations

import re
from typing import Iterable

from src.pipeline.normalize import Article
from src.pipeline.source_quality import publisher_name

_TRAILING_MEDIA_SUFFIX_RE = re.compile(
    r"(?:\s*(?:-|\||·|:)\s*(?:"
    r"연합뉴스|뉴시스|뉴스1|머니투데이|이데일리|매일경제|한국경제|서울경제|조선비즈|아시아경제|"
    r"파이낸셜뉴스|헤럴드경제|중앙일보|조선일보|동아일보|한겨레|경향신문|국민일보|"
    r"세계일보|문화일보|전자신문|디지털타임스|SBS|KBS|MBC|YTN|JTBC|TV조선|채널A|"
    r"(?:[가-힣A-Za-z0-9]+(?:뉴스|일보|신문|경제|방송|TV))"
    r")\s*)$",
    re.IGNORECASE,
)
_DECOR_SUFFIX_RE = re.compile(r"\s*(?:\.{3,}|…+|\[종합\]|\(종합\)|\[속보\]|\(속보\))\s*$")


def normalize_title(title: str) -> str:
    t = (title or "").lower().strip()
    if not t:
        return ""

    t = re.sub(r"[\"'“”‘’`´]", "", t)
    t = re.sub(r"[\[\]{}<>]", " ", t)
    t = re.sub(r"[()（）]", " ", t)
    t = re.sub(r"[!?~^_=+]+", " ", t)

    # 말미 장식성 접미/언론사 꼬리표 제거 (과제 최소 범위)
    t = _DECOR_SUFFIX_RE.sub("", t).strip()
    t = re.sub(r"^(?:속보|종합)\s+", "", t)
    t = re.sub(r"\s+(?:속보|종합)$", "", t)

    # 짧은 말미 토큰(언론사명/데스크 표기) 제거
    # e.g., "... - 연합뉴스", "... | 조선비즈"
    reduced = _TRAILING_MEDIA_SUFFIX_RE.sub("", t).strip()
    if reduced:
        t = reduced

    t = re.sub(r"[.,;:·/\\|-]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _canonical_link(article: Article) -> str:
    return (
        (article.naver_link or "").strip()
        or (article.originallink or "").strip()
        or (article.link or "").strip()
    )


def _prefer_new_article(current: Article, candidate: Article) -> bool:
    # True면 candidate를 대표기사로 채택
    current_ts = getattr(current, "pub_date", None)
    cand_ts = getattr(candidate, "pub_date", None)
    if cand_ts and current_ts and cand_ts != current_ts:
        return cand_ts > current_ts

    current_desc_len = len((current.description or "").strip())
    cand_desc_len = len((candidate.description or "").strip())
    if cand_desc_len != current_desc_len:
        return cand_desc_len > current_desc_len

    current_link = _canonical_link(current)
    cand_link = _canonical_link(candidate)
    if bool(cand_link) != bool(current_link):
        return bool(cand_link)

    return False


def deduplicate(articles: Iterable[Article]) -> list[Article]:
    seen_exact: set[str] = set()
    clusters: dict[str, list[Article]] = {}

    for article in articles:
        canonical_link = _canonical_link(article)
        exact_key = f"{(article.title or '').lower()}|{canonical_link}"
        if exact_key in seen_exact:
            continue
        seen_exact.add(exact_key)

        norm_title = normalize_title(article.title or "")
        article.normalized_title = norm_title
        cluster_key = norm_title or f"title:{(article.title or '').lower().strip()}"
        article.cluster_key = cluster_key
        clusters.setdefault(cluster_key, []).append(article)

    unique: list[Article] = []
    for idx, (cluster_key, items) in enumerate(clusters.items(), start=1):
        rep = items[0]
        for cand in items[1:]:
            if _prefer_new_article(rep, cand):
                rep = cand

        cluster_id = f"c{idx}"
        cluster_size = len(items)
        rep.cluster_key = cluster_key
        rep.cluster_id = cluster_id
        rep.cluster_size = cluster_size
        # 흡수된 기사(다른 출처의 같은 제목 보도)의 메타를 대표에 실어 보낸다 —
        # 버리면 이후 issue_cluster의 관련 기사 목록/개수에서 영영 누락된다.
        rep.duplicate_sources = [
            {
                "title": item.title or "",
                "link": _canonical_link(item),
                # 네이버 API는 언론사명을 주지 않으므로 원문 도메인으로 출처 라벨 유도
                "press": publisher_name(item),
                "pub_date": str(item.pub_date or ""),
            }
            for item in items
            if item is not rep
        ]
        if not rep.normalized_title:
            rep.normalized_title = normalize_title(rep.title or "")
        unique.append(rep)

    return unique
