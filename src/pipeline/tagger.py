from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from src.pipeline.normalize import Article


@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]
    topics: list[str]
    matched_keywords: list[str]


# NOTE: 일부 짧은 키워드는 다른 단어의 접두/부분문자열로 자주 등장해 오탐을 유발함.
# 예) '리스' -> '리스크' (해외/시장 기사에 빈번)
RISKY_SHORT_KEYWORDS = {"대부", "여전"}
_KOR_WORD_CHAR_CLASS = "가-힣A-Za-z0-9"


def _keyword_in_text(keyword: str, text: str) -> bool:
    kw = (keyword or "").lower().strip()
    if not kw:
        return False

    # '리스'는 '리스크'에서 매우 자주 등장해 여전(리스/할부)로 오분류를 유발.
    # - 자동차리스/리스료 등은 그대로 잡되, '리스크'는 제외.
    if kw == "리스":
        return re.search(r"리스(?!크)", text) is not None

    # 영문/숫자 짧은 토큰(PF/CP/IPO/ABCP/FOMC 등)은 단어 경계로 매칭.
    # - 예: cp 가 cpi 에서 잡히는 문제 방지
    if re.fullmatch(r"[a-z0-9]+", kw) and len(kw) <= 4:
        return re.search(rf"\b{re.escape(kw)}\b", text) is not None

    if kw in RISKY_SHORT_KEYWORDS:
        pattern = rf"(?<![{_KOR_WORD_CHAR_CLASS}]){re.escape(kw)}(?![{_KOR_WORD_CHAR_CLASS}])"
        return re.search(pattern, text) is not None

    return kw in text


def _collect_hits(keywords: list[str], text: str) -> list[str]:
    hits = []
    for kw in keywords:
        if _keyword_in_text(kw, text):
            hits.append(kw)
    return hits


def tag_articles(
    articles: list[Article],
    sector_queries: dict[str, list[str]],
    topic_queries: dict[str, list[str]] | None = None,
) -> list[TaggedArticle]:
    topic_queries = topic_queries or {}

    tagged: list[TaggedArticle] = []
    for article in articles:
        title_text = (article.title or "").lower()
        desc_text = (article.description or "").lower()
        query_text = (getattr(article, "query", "") or "").lower()
        full_text = f"{title_text} {desc_text} {query_text}"

        # -----------------------
        # Sector: best 1 (제목/요약 기반)
        # -----------------------
        best_sector = "기타"
        best_score = 0
        best_hits: list[str] = []
        for sector, keywords in sector_queries.items():
            title_hits = _collect_hits(keywords, title_text)
            desc_hits = _collect_hits(keywords, desc_text)

            # 섹터는 '제목/요약' 기반으로만 판정 (query는 수집용 문자열이라 편향을 줄 수 있음)
            score = (len(title_hits) * 2) + len(desc_hits)
            if score > best_score:
                best_score = score
                best_sector = sector
                best_hits = [
                    *title_hits,
                    *[kw for kw in desc_hits if kw not in title_hits],
                ]

        sectors = [best_sector] if best_score > 0 else ["기타"]

        # -----------------------
        # Topics: multi
        # -----------------------
        topics: list[str] = []
        topic_hits_all: list[str] = []
        for topic, keywords in topic_queries.items():
            hits = _collect_hits(keywords, full_text)
            if hits:
                topics.append(topic)
                topic_hits_all.extend(hits)

        matched_keywords = list(dict.fromkeys([*best_hits, *topic_hits_all]))

        tagged.append(
            TaggedArticle(
                article=article,
                sectors=sectors,
                topics=topics,
                matched_keywords=matched_keywords,
            )
        )

    return tagged


def keyword_trends(tagged: list[TaggedArticle], top_n: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for item in tagged:
        counter.update(item.matched_keywords)
    return counter.most_common(top_n)
