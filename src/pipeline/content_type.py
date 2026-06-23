from __future__ import annotations

import re
from typing import Any, Literal

ContentType = Literal[
    "hard_news",
    "regulatory",
    "risk",
    "market",
    "product",
    "schedule",
    "opinion",
    "profile",
    "event",
    "pr",
    "local_social",
    "briefing",
    "price_quote",
]


def _field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _article(item: Any) -> Any:
    return _field(item, "article") or item


def _list_field(item: Any, key: str) -> list[str]:
    value = _field(item, key)
    if value is None and _field(item, "article") is not None:
        value = _field(_field(item, "article"), key)
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return []


def _text(item: Any) -> str:
    article = _article(item)
    parts = [
        _field(article, "title"),
        _field(article, "description"),
        _field(article, "summary"),
        _field(article, "relevance_label"),
        " ".join(_list_field(item, "sectors")),
        " ".join(_list_field(item, "topics")),
        " ".join(_list_field(item, "matched_keywords")),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _title_text(item: Any) -> str:
    article = _article(item)
    return str(_field(article, "title") or "").lower()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _has_regulator(text: str) -> bool:
    if _has_any(text, ("금융위원회", "금융감독원", "금감원", "금융당국", "fiu")):
        return True
    return re.search(r"금융위(?!기|험)", text) is not None


def _has_profile_signal(text: str) -> bool:
    if _has_any(text, ("인터뷰", "프로필", "선임", "취임", "ceo", "대표이사", "임원")):
        return True
    return re.search(r"(?<!개)인사(?!업|자|신용|대출)", text) is not None


REGULATORS = ("금융위", "금융위원회", "금융감독원", "금감원", "금융당국", "fiu")
REGULATORY_ACTIONS = (
    "검사", "제재", "과징금", "징계", "행정처분", "현장점검", "시정명령", "단속", "고발", "적발", "착수", "불완전판매"
)
RISK_TERMS = (
    "불법사금융", "불법대부", "불법 대출광고", "대출광고", "불법추심", "채권추심", "보이스피싱", "스미싱", "대포통장", "연체율", "연체", "부실채권", "부실",
    "pf", "유동성", "익스포저", "워크아웃", "건전성", "충당금", "자본확충", "고리대금", "사기", "피해 확산"
)
POLICY_TERMS = ("정책", "규제", "대책", "제도", "입법예고", "시행령", "가계대출", "최고금리")
MARKET_TOPICS = ("해외·글로벌", "환율·외환", "자금시장·유동성", "물가·경기지표", "금리·수수료·최고금리", "거시·시장")
MARKET_TERMS = ("fomc", "cpi", "pce", "연준", "기준금리", "국채금리", "환율", "외환", "물가", "자금시장", "유동성")
PRODUCT_TERMS = ("예금금리", "수신금리", "대출금리", "마이너스통장", "마통", "주담대", "카드론", "금융상품", "특판", "우대금리")
SCHEDULE_TERMS = ("다음주", "이번주", "주요일정", "주요 일정", "회의 일정", "브리핑 일정")
OPINION_TERMS = ("칼럼", "사설", "기고", "기자수첩", "시론", "데스크", "전문가 진단")
BRIEFING_TERMS = ("금융 브리핑", "오늘의 은행", "금융권 소식", "단신", "브리핑", "금융 레이더", "업계 소식")
LOCAL_SOCIAL_TERMS = ("사회공헌", "기부", "후원", "봉사", "장학금", "지역사회", "취약계층")
EVENT_TERMS = ("행사", "이벤트", "캠페인", "공모전", "세미나", "설명회", "컨퍼런스")
PR_TERMS = ("업무협약", "mou", "홍보", "출시 기념", "수상", "선정", "인증", "브랜드")
PROFILE_TERMS = ("인터뷰", "프로필", "인사", "선임", "취임", "ceo", "대표이사", "임원")
PRICE_QUOTE_TERMS = ("장 마감", "마감시황", "상승 마감", "하락 마감", "혼조 마감", "원달러 환율 마감", "코스피 마감", "코스닥 마감", "비트코인 신고가", "암호화폐 랠리", "코인 시세")
FINANCE_ANCHORS = ("은행", "저축은행", "금융", "보험", "카드", "캐피탈", "대부", "금감원", "금융위", "가계대출", "pf")


def classify_content_type(item: Any) -> ContentType:
    """Classify a tagged finance-news item for Top 10 ranking adjustments."""
    text = _text(item)
    title = _title_text(item)
    topics = " ".join(_list_field(item, "topics"))

    # Format labels that are explicit in the title should win over weaker anchors.
    if _has_any(text, OPINION_TERMS):
        return "opinion"

    strong_regulatory = (_has_regulator(text) or "감독·제재" in text) and _has_any(text, REGULATORY_ACTIONS)
    strong_risk = _has_any(text, RISK_TERMS)
    if _has_any(title, SCHEDULE_TERMS) and not (strong_regulatory or strong_risk):
        return "schedule"
    if _has_profile_signal(text) and not _has_any(text, REGULATORY_ACTIONS + RISK_TERMS):
        return "profile"

    if strong_regulatory:
        return "regulatory"
    if strong_risk:
        return "risk"

    if _has_any(text, LOCAL_SOCIAL_TERMS):
        return "local_social"
    if _has_any(title, BRIEFING_TERMS):
        return "briefing"
    if _has_any(text, EVENT_TERMS):
        return "event"
    if _has_any(text, PR_TERMS):
        return "pr"
    if _has_any(text, PRICE_QUOTE_TERMS):
        return "price_quote"
    if _has_any(text, PRODUCT_TERMS):
        return "product"
    if _has_any(text, MARKET_TERMS) or any(topic in topics for topic in MARKET_TOPICS):
        return "market"
    if _has_any(text, POLICY_TERMS) or _has_any(text, FINANCE_ANCHORS):
        return "hard_news"
    return "hard_news"
