from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

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

TITLE_STRONG_SCORE = 6
TITLE_WEAK_SCORE = 3
TITLE_GENERIC_SCORE = 1
BODY_STRONG_SCORE = 3
BODY_WEAK_SCORE = 1
BODY_GENERIC_SCORE = 1
TITLE_NEGATIVE_PENALTY = 6
BODY_NEGATIVE_PENALTY = 3
PRIMARY_SECTOR_THRESHOLD = 4

TOPIC_TITLE_STRONG_SCORE = 4
TOPIC_TITLE_WEAK_SCORE = 2
TOPIC_BODY_STRONG_SCORE = 2
TOPIC_BODY_WEAK_SCORE = 1
TOPIC_QUERY_AUX_SCORE = 0.15
TOPIC_TITLE_NEGATIVE_PENALTY = 4
TOPIC_BODY_NEGATIVE_PENALTY = 2
DEFAULT_TOPIC_THRESHOLD = 3.0

GENERIC_SECTOR_TOKENS = {
    "은행",
    "인터넷은행",
    "은행권",
    "펀드",
    "거래소",
    "대출",
}

TEXT_ALIASES: tuple[tuple[str, str], ...] = (
    ("investment bank", "투자은행"),
    ("investment banking", "투자은행"),
    ("인터넷 전문은행", "인터넷은행"),
    ("가상화폐", "암호화폐"),
    ("코인거래소", "가상자산 거래소"),
)

SECTOR_RULE_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "은행": {
        "strong": [
            "시중은행",
            "예대금리차",
            "예금은행",
            "은행채",
            "은행권",
            "인터넷은행",
            "국책은행",
            "kb국민은행",
            "국민은행",
            "신한은행",
            "우리은행",
            "하나은행",
            "ibk기업은행",
            "기업은행",
            "nh농협은행",
            "농협은행",
            "sc제일은행",
            "씨티은행",
            "산업은행",
            "수출입은행",
            "부산은행",
            "경남은행",
            "광주은행",
            "전북은행",
            "대구은행",
            "카카오뱅크",
            "케이뱅크",
            "토스뱅크",
        ],
        "weak": ["은행", "인터넷은행", "시중은행", "국책은행"],
        "negative": ["투자은행", "저축은행", "상호저축은행", "ipo", "회사채", "ecm", "dcm"],
    },
    "IB·자본시장": {
        "strong": ["투자은행", "ipo", "ecm", "dcm", "회사채", "abcp"],
        "weak": ["cp", "m&a"],
        "negative": ["시중은행", "예대금리차", "인터넷은행"],
    },
    "자산운용·연기금": {
        "strong": ["자산운용", "운용사", "국민연금", "연기금"],
        "weak": ["etf", "펀드"],
        "negative": [],
    },
    "디지털자산": {
        "strong": ["가상자산", "암호화폐", "토큰증권", "sto"],
        "weak": ["거래소"],
        "negative": ["한국거래소", "유가증권시장", "코스닥"],
    },
    "핀테크·플랫폼": {
        "strong": ["핀테크", "마이데이터", "간편결제", "금융플랫폼"],
        "weak": ["대출비교", "대출모집", "pg", "대출"],
        "negative": [],
    },
}

TOPIC_RULE_OVERRIDES: dict[str, dict[str, Any]] = {
    "해외·글로벌": {
        "strong": ["연준", "fomc", "ecb", "boj"],
        "weak": ["미국", "유럽", "중국", "달러", "환율", "뉴욕", "월가", "국채"],
        "negative": ["국내", "금융위", "금감원", "저축은행", "대부업"],
        "threshold": 5.0,
    },
    "금리·수수료·최고금리": {
        "strong": ["최고금리", "기준금리"],
        "weak": ["금리", "수수료"],
        "negative": [],
        "threshold": 4.0,
    },
    "규제·가계부채": {
        "strong": ["가계부채", "대출규제", "총부채"],
        "weak": ["dsr", "ltv"],
        "negative": [],
        "threshold": 4.0,
    },
}


def _normalize_text(text: str) -> str:
    normalized = text
    for src, dst in TEXT_ALIASES:
        normalized = normalized.replace(src, dst)
    return normalized


def _unique_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _build_sector_rules(sector: str, keywords: list[str]) -> dict[str, list[str]]:
    override = SECTOR_RULE_OVERRIDES.get(sector, {})
    base_strong = [kw for kw in keywords if kw not in GENERIC_SECTOR_TOKENS]
    strong = _unique_keep_order(base_strong + override.get("strong", []))
    weak = _unique_keep_order([kw for kw in keywords if kw not in strong] + override.get("weak", []))
    negative = _unique_keep_order(override.get("negative", []))
    return {
        "strong": strong,
        "weak": weak,
        "generic": [kw for kw in weak if kw in GENERIC_SECTOR_TOKENS],
        "negative": negative,
    }


def _score_sector(title_text: str, desc_text: str, rules: dict[str, list[str]]) -> tuple[int, int, list[str]]:
    title_strong_hits = _collect_hits(rules["strong"], title_text)
    desc_strong_hits = _collect_hits(rules["strong"], desc_text)
    title_weak_hits = _collect_hits(rules["weak"], title_text)
    desc_weak_hits = _collect_hits(rules["weak"], desc_text)
    title_generic_hits = _collect_hits(rules["generic"], title_text)
    desc_generic_hits = _collect_hits(rules["generic"], desc_text)
    title_negative_hits = _collect_hits(rules["negative"], title_text)
    desc_negative_hits = _collect_hits(rules["negative"], desc_text)

    title_score = (
        len(title_strong_hits) * TITLE_STRONG_SCORE
        + len(
            [
                kw
                for kw in title_weak_hits
                if kw not in title_strong_hits and kw not in title_generic_hits
            ]
        )
        * TITLE_WEAK_SCORE
        + len(title_generic_hits) * TITLE_GENERIC_SCORE
        - len(title_negative_hits) * TITLE_NEGATIVE_PENALTY
    )
    body_score = (
        len([kw for kw in desc_strong_hits if kw not in title_strong_hits]) * BODY_STRONG_SCORE
        + len(
            [
                kw
                for kw in desc_weak_hits
                if kw not in title_weak_hits and kw not in desc_generic_hits
            ]
        )
        * BODY_WEAK_SCORE
        + len([kw for kw in desc_generic_hits if kw not in title_generic_hits]) * BODY_GENERIC_SCORE
        - len(desc_negative_hits) * BODY_NEGATIVE_PENALTY
    )
    score = title_score + body_score

    positive_hits = _unique_keep_order(
        [
            *title_strong_hits,
            *title_weak_hits,
            *desc_strong_hits,
            *desc_weak_hits,
        ]
    )
    return score, title_score, positive_hits


def _build_topic_rules(topic: str, keywords: list[str]) -> dict[str, Any]:
    override = TOPIC_RULE_OVERRIDES.get(topic, {})
    strong = _unique_keep_order(override.get("strong", []))
    weak = _unique_keep_order([kw for kw in keywords if kw not in strong] + override.get("weak", []))
    negative = _unique_keep_order(override.get("negative", []))
    threshold = float(override.get("threshold", DEFAULT_TOPIC_THRESHOLD))
    return {
        "strong": strong or keywords,
        "weak": weak,
        "negative": negative,
        "threshold": threshold,
    }


def _score_topic(
    title_text: str,
    body_text: str,
    query_text: str,
    rules: dict[str, Any],
) -> tuple[float, list[str]]:
    title_strong_hits = _collect_hits(rules["strong"], title_text)
    body_strong_hits = _collect_hits(rules["strong"], body_text)
    title_weak_hits = _collect_hits(rules["weak"], title_text)
    body_weak_hits = _collect_hits(rules["weak"], body_text)
    title_negative_hits = _collect_hits(rules["negative"], title_text)
    body_negative_hits = _collect_hits(rules["negative"], body_text)

    content_hits = {
        *title_strong_hits,
        *body_strong_hits,
        *title_weak_hits,
        *body_weak_hits,
    }
    query_hits = [
        kw
        for kw in _collect_hits([*rules["strong"], *rules["weak"]], query_text)
        if kw not in content_hits
    ]

    score = (
        len(title_strong_hits) * TOPIC_TITLE_STRONG_SCORE
        + len([kw for kw in body_strong_hits if kw not in title_strong_hits]) * TOPIC_BODY_STRONG_SCORE
        + len([kw for kw in title_weak_hits if kw not in title_strong_hits]) * TOPIC_TITLE_WEAK_SCORE
        + len(
            [
                kw
                for kw in body_weak_hits
                if kw not in title_weak_hits and kw not in body_strong_hits and kw not in title_strong_hits
            ]
        )
        * TOPIC_BODY_WEAK_SCORE
        + len(query_hits) * TOPIC_QUERY_AUX_SCORE
        - len(title_negative_hits) * TOPIC_TITLE_NEGATIVE_PENALTY
        - len(body_negative_hits) * TOPIC_BODY_NEGATIVE_PENALTY
    )
    hits = _unique_keep_order([*title_strong_hits, *title_weak_hits, *body_strong_hits, *body_weak_hits])
    return score, hits


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

    if kw == "은행":
        return re.search(r"(?<![가-힣a-z0-9])은행(?![가-힣a-z0-9])", text) is not None

    if kw in {"거래소", "펀드", "대출"}:
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
        title_text = _normalize_text((article.title or "").lower())
        desc_text = _normalize_text((article.description or "").lower())
        query_text = (getattr(article, "query", "") or "").lower()
        body_sources = [
            desc_text,
            _normalize_text((getattr(article, "summary", "") or "").lower()),
            _normalize_text((getattr(article, "full_text", "") or "").lower()),
            _normalize_text((getattr(article, "main_text", "") or "").lower()),
            _normalize_text((getattr(article, "content", "") or "").lower()),
        ]
        body_text = " ".join(x for x in body_sources if x).strip()

        # -----------------------
        # Sector: best 1 (제목/요약 기반)
        # -----------------------
        best_sector = "기타"
        best_score = float("-inf")
        best_title_score = float("-inf")
        best_hits: list[str] = []
        for sector, keywords in sector_queries.items():
            rules = _build_sector_rules(sector, keywords)
            score, title_score, hits = _score_sector(title_text, desc_text, rules)

            # 제목 강신호를 우선하고, 이후 총점으로 tie-break.
            if (title_score, score) > (best_title_score, best_score):
                best_score = score
                best_title_score = title_score
                best_sector = sector
                best_hits = hits

        sectors = [best_sector] if best_score >= PRIMARY_SECTOR_THRESHOLD else ["기타"]

        # -----------------------
        # Topics: multi
        # -----------------------
        topics: list[str] = []
        topic_hits_all: list[str] = []
        for topic, keywords in topic_queries.items():
            rules = _build_topic_rules(topic, keywords)
            score, hits = _score_topic(title_text, body_text, query_text, rules)
            if score >= rules["threshold"] and hits:
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
