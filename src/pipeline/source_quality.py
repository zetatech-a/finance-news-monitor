from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from src.pipeline.content_type import classify_content_type
from src.pipeline.fields import field_value as _field, unwrap_article as _article

SourceQuality = Literal[
    "primary",
    "regulatory",
    "major_finance",
    "major_general",
    "specialist",
    "regional",
    "unknown",
    "low_information",
    "promo_or_stock_snippet",
    "press_release_like",
]

PUBLISHER_FIELDS = (
    "press",
    "publisher",
    "source",
    "media",
    "provider",
    "outlet",
    "office",
    "company",
)
LINK_FIELDS = ("originallink", "link", "naver_link", "url")

REGULATORY_TERMS = (
    "금융위원회",
    "금융위",
    "금융감독원",
    "금감원",
    "한국은행",
    "예금보험공사",
    "금융정보분석원",
    "fiu",
)
PRIMARY_OFFICIAL_TERMS = (
    "금융회사 공식",
    "은행 공식",
    "금융지주 공식",
    "공식 홈페이지",
    "공식 블로그",
    "ir자료",
    "ir 자료",
)
PRIMARY_PUBLISHER_TERMS = ("공식", "dart", "전자공시", "한국거래소", "거래소공시")
DISCLOSURE_TERMS = ("공시", "전자공시", "거래소공시")
MAJOR_FINANCE_TERMS = (
    "매일경제",
    "한국경제",
    "서울경제",
    "이데일리",
    "머니투데이",
    "파이낸셜뉴스",
    "아시아경제",
    "헤럴드경제",
    "비즈워치",
    "조선비즈",
    "뉴스핌",
    "연합인포맥스",
    "더벨",
    "대한금융신문",
    "전자신문",
    "디지털데일리",
    "경제",
    "비즈",
    "금융",
    "finance",
    "business",
    "biz",
)
MAJOR_GENERAL_TERMS = (
    "연합뉴스",
    "뉴시스",
    "뉴스1",
    "kbs",
    "mbc",
    "sbs",
    "jtbc",
    "ytn",
    "조선일보",
    "중앙일보",
    "동아일보",
    "한겨레",
    "경향신문",
)
SPECIALIST_TERMS = (
    "보험매일",
    "보험신보",
    "은행연합",
    "저축은행",
    "핀테크",
    "블록체인",
    "코인데스크",
    "코인텔레그래프",
    "토큰포스트",
    "법률신문",
)
REGIONAL_TERMS = (
    "부산",
    "대구",
    "광주",
    "대전",
    "울산",
    "경기",
    "인천",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
    "지역",
)
LOW_INFO_TERMS = ("종합", "단신", "한줄뉴스", "오늘의", "주요뉴스", "브리핑", "모음")
PROMO_STOCK_TERMS = (
    "특징주",
    "급등주",
    "상한가",
    "하한가",
    "투자자 관심",
    "매수",
    "추천주",
    "테마주",
    "관련주",
)
PRESS_RELEASE_TERMS = (
    "보도자료",
    "홍보",
    "출시 기념",
    "수상",
    "선정",
    "인증",
    "업무협약",
    "mou",
    "캠페인",
    "행사",
    "기부",
    "후원",
    "사회공헌",
)

ADJUSTMENTS: dict[str, float] = {
    "regulatory": 0.8,
    "primary": 0.6,
    "major_finance": 0.4,
    "major_general": 0.2,
    "specialist": 0.1,
    "regional": -0.2,
    "unknown": -0.3,
    "low_information": -0.8,
    "press_release_like": -0.9,
    "promo_or_stock_snippet": -1.2,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_publisher_name(name: str) -> str:
    value = _clean(name)
    if not value:
        return ""
    value = re.sub(r"^[\[\(【〈<\s]+|[\]\)】〉>\s]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*[|｜]\s*네이버뉴스\s*$", "", value).strip()
    value = re.sub(r"\s*-\s*뉴스\s*$", "", value).strip()
    value = re.sub(r"\s*언론사\s*$", "", value).strip()
    return value


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _fallback_domain(article: Any) -> str:
    for key in LINK_FIELDS:
        domain = _domain_from_url(_clean(_field(article, key)))
        if domain and "naver.com" not in domain:
            return domain
    return ""


def publisher_name(article_or_item: Any) -> str:
    article = _article(article_or_item)
    for key in PUBLISHER_FIELDS:
        value = normalize_publisher_name(_clean(_field(article, key)))
        if value:
            return value
    return _fallback_domain(article)


def _text(article_or_item: Any) -> str:
    article = _article(article_or_item)
    parts = [publisher_name(article_or_item), _fallback_domain(article)]
    for key in ("title", "description", "summary", "body", "content"):
        parts.append(_clean(_field(article, key)))
    return " ".join(p for p in parts if p).lower()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _is_strong_regulatory_or_risk(item: Any) -> bool:
    return classify_content_type(item) in {"regulatory", "risk"}


def classify_source_quality(article_or_item: Any) -> SourceQuality:
    text = _text(article_or_item)
    publisher = publisher_name(article_or_item).lower()
    if _has_any(text, REGULATORY_TERMS):
        return "regulatory"

    strong_reg_or_risk = _is_strong_regulatory_or_risk(article_or_item)
    publisher_is_primary = _has_any(publisher, PRIMARY_PUBLISHER_TERMS)
    has_official_primary_text = _has_any(text, PRIMARY_OFFICIAL_TERMS)
    has_primary_disclosure = publisher_is_primary and _has_any(text, DISCLOSURE_TERMS)
    if publisher_is_primary or has_official_primary_text or has_primary_disclosure:
        return "primary"
    if _has_any(text, PROMO_STOCK_TERMS):
        return "promo_or_stock_snippet"
    if _has_any(text, PRESS_RELEASE_TERMS) and not strong_reg_or_risk:
        return "press_release_like"
    if _has_any(text, LOW_INFO_TERMS) and not strong_reg_or_risk:
        return "low_information"

    if not publisher:
        return "unknown"
    if _has_any(publisher, MAJOR_FINANCE_TERMS):
        return "major_finance"
    if _has_any(publisher, MAJOR_GENERAL_TERMS):
        return "major_general"
    if _has_any(publisher, SPECIALIST_TERMS):
        return "specialist"
    if _has_any(publisher, REGIONAL_TERMS):
        return "regional"
    return "unknown"


def source_quality_rank_adjustment(article_or_item: Any) -> float:
    quality = classify_source_quality(article_or_item)
    adjustment = ADJUSTMENTS.get(quality, 0.0)
    if quality == "unknown" and _is_strong_regulatory_or_risk(article_or_item):
        return max(adjustment, -0.1)
    return adjustment
