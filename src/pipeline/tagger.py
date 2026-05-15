from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.pipeline.normalize import Article
from src.pipeline.text_matcher import contains_term, has_any_term, normalize_text


@dataclass
class TaggedArticle:
    article: Article
    sectors: list[str]
    topics: list[str]
    matched_keywords: list[str]


# NOTE: 일부 짧은 키워드는 다른 단어의 접두/부분문자열로 자주 등장해 오탐을 유발함.
# 예) '리스' -> '리스크' (해외/시장 기사에 빈번)
RISKY_SHORT_KEYWORDS = {"대부", "여전"}

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
TOPIC_TITLE_CONTEXT_SCORE = 1.2
TOPIC_BODY_CONTEXT_SCORE = 0.6
TOPIC_SECTOR_AUX_SCORE = 0.8
DEFAULT_TOPIC_THRESHOLD = 2.8

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
            "은행권 대출",
            "은행권 수익",
            "은행권 예대금리차",
        ],
        "weak": ["은행", "인터넷은행", "시중은행", "국책은행"],
        "negative": ["투자은행", "저축은행", "상호저축은행", "ipo", "회사채", "ecm", "dcm"],
    },
    "보험": {
        "strong": [
            "보험사",
            "보험업계",
            "손해보험",
            "생명보험",
            "손보사",
            "생보사",
            "실손보험",
            "자동차보험",
            "킥스",
            "K-ICS",
        ],
        "weak": ["보험"],
        "negative": ["건강보험", "고용보험", "산재보험", "재보험", "보험료"],
        "demote_to_weak": ["보험"],
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
    "여전": {
        "strong": ["여신협회", "여신금융협회", "여신전문금융협회", "카드수수료"],
        "weak": ["여전"],
        "negative": [],
        "demote_to_weak": ["여전"],
    },
    "핀테크·플랫폼": {
        "strong": ["핀테크", "마이데이터", "간편결제", "금융플랫폼"],
        "weak": ["대출비교", "대출모집", "pg", "대출"],
        "negative": [],
    },
    "거시·시장": {
        "strong": ["증시", "코스피", "코스닥", "채권", "외화채", "유가", "환율", "금리"],
        "weak": [],
        "negative": [],
    },
    "감독·제재": {
        "strong": ["검사", "제재", "징계", "제재심", "위반", "적발", "조사", "시정명령", "과징금", "행정처분"],
        "weak": ["금융위", "금감원", "한국은행"],
        "negative": [],
        "demote_to_weak": ["금융위", "금감원", "한국은행"],
    },
    "입법·정책": {
        "strong": [
            "시행령",
            "시행규칙",
            "입법예고",
            "제도 개편",
            "규제 완화",
            "규제 강화",
            "정책 발표",
            "방안 발표",
            "개정안",
            "가이드라인",
            "대책",
        ],
        "weak": ["금융위", "금감원", "한국은행"],
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
        "strong": ["최고금리", "기준금리", "금리 산정", "가산금리", "중도상환수수료"],
        "weak": ["금리", "수수료"],
        "negative": [],
        "threshold": 3.0,
    },
    "규제·가계부채": {
        "strong": ["가계부채", "대출규제", "총부채"],
        "weak": ["dsr", "ltv", "총부채원리금상환비율", "주택담보인정비율"],
        "negative": [],
        "threshold": 3.6,
    },
}

TOPIC_CONTEXT_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "금리·수수료·최고금리": {
        "title": ("금리 산정", "가산금리", "우대금리", "최고금리", "중도상환수수료", "보증료", "기준금리"),
        "body": ("금리 체계", "금리 개편", "수수료율", "산정 방식"),
    },
    "연체·부실": {
        "title": ("연체율", "연체", "부실", "고정이하여신", "부실채권", "npl"),
        "body": ("건전성", "충당금", "상각", "고정이하"),
    },
    "부동산·PF": {
        "title": ("부동산 pf", "pf", "프로젝트파이낸싱", "브릿지론", "재구조화"),
        "body": ("미분양", "토지담보", "pf 사업장"),
    },
    "규제·가계부채": {
        "title": ("가계부채", "대출규제", "dsr", "ltv", "총부채"),
        "body": ("총부채원리금상환비율", "주택담보인정비율", "스트레스 dsr", "규제 완화", "규제 강화"),
    },
    "불법사금융·불법추심·보이스피싱": {
        "title": ("불법사금융", "불법추심", "보이스피싱", "미등록대부", "피해 확산"),
        "body": ("피해구제", "대포통장", "스미싱", "불법 대출광고"),
    },
    "서민금융·대환·채무조정": {
        "title": ("서민금융", "대환", "채무조정", "햇살론", "신복위"),
        "body": ("개인회생", "워크아웃", "출연금", "정책서민금융"),
    },
    "자금시장·유동성": {
        "title": ("자금시장", "유동성", "cp", "abcp", "회사채"),
        "body": ("단기자금", "스프레드", "유동성 지원"),
    },
    "자산운용·연기금": {
        "title": ("자산운용", "운용사", "연기금", "국민연금"),
        "body": ("etf", "펀드"),
    },
    "디지털자산": {
        "title": ("가상자산", "디지털자산", "암호화폐", "토큰증권", "sto"),
        "body": ("거래소", "코인"),
    },
}

TOPIC_SECTOR_AFFINITY: dict[str, tuple[str, ...]] = {
    "연체·부실": ("대부", "은행", "저축은행", "상호금융", "여전"),
    "부동산·PF": ("저축은행", "IB·자본시장", "여전", "은행"),
    "규제·가계부채": ("은행", "입법·정책", "감독·제재"),
    "불법사금융·불법추심·보이스피싱": ("대부", "감독·제재", "입법·정책"),
    "서민금융·대환·채무조정": ("대부", "은행", "입법·정책", "감독·제재"),
    "자금시장·유동성": ("IB·자본시장", "거시·시장"),
    "자산운용·연기금": ("자산운용·연기금",),
    "디지털자산": ("디지털자산",),
}


def _normalize_text(text: str) -> str:
    normalized = normalize_text(text)
    for src, dst in TEXT_ALIASES:
        normalized = normalized.replace(src, dst)
    return normalized


def _unique_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _build_sector_rules(sector: str, keywords: list[str]) -> dict[str, list[str]]:
    override = SECTOR_RULE_OVERRIDES.get(sector, {})
    demote_to_weak = set(override.get("demote_to_weak", []))
    base_strong = [kw for kw in keywords if kw not in GENERIC_SECTOR_TOKENS and kw not in demote_to_weak]
    strong = _unique_keep_order(base_strong + override.get("strong", []))
    weak = _unique_keep_order([kw for kw in keywords if kw not in strong] + list(demote_to_weak) + override.get("weak", []))
    negative = _unique_keep_order(override.get("negative", []))
    return {
        "strong": strong,
        "weak": weak,
        "generic": [kw for kw in weak if kw in GENERIC_SECTOR_TOKENS],
        "negative": negative,
    }


def _is_schedule_article(text: str) -> bool:
    schedule_patterns = (
        "다음주",
        "이번주",
        "주간",
        "일정",
        "브리핑",
        "회의 일정",
        "주요일정",
    )
    return has_any_term(text, schedule_patterns)


def _has_regulator_anchor(text: str) -> bool:
    regulator_anchors = ("금융위", "금융위원회", "금감원", "금융감독원")
    return has_any_term(text, regulator_anchors)


def _has_supervisory_action(text: str) -> bool:
    strong_signals = (
        "검사 착수",
        "현장점검",
        "개선명령",
        "시정명령",
        "행정처분",
        "불완전판매",
        "중징계",
        "경징계",
        "제재심",
        "과징금",
        "검사",
        "징계",
        "제재",
        "위반",
        "적발",
        "조사",
    )
    return has_any_term(text, strong_signals)


def _has_policy_signal(text: str) -> bool:
    policy_signals = (
        "제도개선",
        "제도 개선",
        "시행령",
        "시행규칙",
        "입법예고",
        "제도 개편",
        "정책 발표",
        "방안 발표",
        "개정",
        "가이드라인",
        "대책",
        "규제 완화",
        "규제 강화",
        "체계 개편",
        "법안",
        "규정",
        "정책",
        "발표",
        "추진",
    )
    return has_any_term(text, policy_signals)


def _has_bank_identity(text: str) -> bool:
    bank_signals = (
        "우리은행",
        "신한은행",
        "국민은행",
        "하나은행",
        "기업은행",
        "농협은행",
        "카카오뱅크",
        "케이뱅크",
        "토스뱅크",
        "은행권",
        "시중은행",
        "인터넷은행",
        "국책은행",
    )
    if has_any_term(text, bank_signals):
        return "투자은행" not in text and "저축은행" not in text and "상호저축은행" not in text
    return False


def _has_market_context(text: str) -> bool:
    market_patterns = (
        "외환시장",
        "금융시장",
        "채권시장",
        "국채금리",
        "국고채",
        "원달러 환율",
        "원/달러",
        "코스피",
        "코스닥",
        "증시",
        "한국은행",
        "한은",
        "기준금리",
        "연준",
        "fomc",
        "시장금리",
        "환율 전망",
        "채권금리",
        "스프레드",
        "마감시황",
        "장 마감",
        "뉴욕증시",
        "나스닥",
        "s&p",
    )
    return has_any_term(text, market_patterns)


def _has_market_title_signal(title_text: str) -> bool:
    return _has_market_context(title_text)


def _has_generic_macro_term(text: str) -> bool:
    generic_terms = ("환율", "금리", "유가", "달러", "원자재")
    return has_any_term(text, generic_terms)


def _has_corporate_earnings_context(text: str) -> bool:
    corporate_terms = (
        "영업이익",
        "매출",
        "순이익",
        "실적",
        "원가",
        "원재료",
        "수출",
        "수주",
        "항공",
        "조선",
        "식품",
        "바이오",
        "게임",
        "자동차",
        "반도체",
        "해운",
        "철강",
    )
    return has_any_term(text, corporate_terms)


def _has_financial_company_context(text: str) -> bool:
    financial_terms = (
        "은행",
        "뱅크",
        "저축은행",
        "보험",
        "보험사",
        "카드",
        "카드사",
        "카드론",
        "캐피탈",
        "여전",
        "증권",
        "증권사",
        "자산운용",
        "운용사",
        "대부업",
        "상호금융",
        "새마을금고",
        "신협",
        "금융권",
        "금융회사",
        "핀테크",
    )
    return has_any_term(text, financial_terms)


def _has_bank_quote_source_signal(text: str) -> bool:
    quote_patterns = (
        "딜링룸",
        "연구원",
        "관계자",
        "증권가",
        "시장 참가자",
        "트레이더",
        "외환 딜러",
    )
    return has_any_term(text, quote_patterns)


def _has_explicit_bank_brand(text: str) -> bool:
    explicit_bank_brands = (
        "우리은행",
        "신한은행",
        "국민은행",
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
    )
    return has_any_term(text, explicit_bank_brands)


def _apply_sector_adjustments(
    title_text: str,
    desc_text: str,
    sector_scores: dict[str, int],
) -> dict[str, float]:
    adjusted: dict[str, float] = {k: float(v) for k, v in sector_scores.items()}
    configured = set(adjusted)
    combined = f"{title_text} {desc_text}".strip()
    has_schedule = _is_schedule_article(combined)
    has_action = _has_supervisory_action(combined)
    has_regulator = _has_regulator_anchor(combined)
    has_policy = _has_policy_signal(combined)
    has_market_context = _has_market_context(combined)
    has_generic_macro = _has_generic_macro_term(combined)
    has_corporate_context = _has_corporate_earnings_context(combined)
    has_financial_context = _has_financial_company_context(combined)
    has_bank_title = _has_bank_identity(title_text)
    has_bank_desc = _has_bank_identity(desc_text)
    has_market_title = _has_market_title_signal(title_text)
    has_bank_quote_source = _has_bank_quote_source_signal(combined)
    has_explicit_bank_title = _has_explicit_bank_brand(title_text)
    has_bank_subject_title = has_bank_title and (has_explicit_bank_title or not (has_market_title and _has_bank_quote_source_signal(title_text)))

    if "은행" in configured:
        if has_bank_subject_title:
            adjusted["은행"] += 5.0
        elif has_bank_desc:
            adjusted["은행"] += 1.5
        if has_bank_quote_source:
            adjusted["은행"] -= 2.5
    if has_market_title and "거시·시장" in configured:
        adjusted["거시·시장"] += 5.0
        if "은행" in configured and (has_bank_quote_source or has_bank_desc) and not has_bank_subject_title:
            adjusted["은행"] -= 4.0
    if "거시·시장" in configured and has_generic_macro and not has_market_context:
        if has_corporate_context and not has_financial_context:
            adjusted["거시·시장"] -= 10.0
        elif has_financial_context:
            adjusted["거시·시장"] -= 8.0
        else:
            adjusted["거시·시장"] -= 5.0

    if has_schedule:
        for sector in ("감독·제재", "입법·정책", "거시·시장"):
            if sector in configured:
                adjusted[sector] -= 1.2
        if not has_action and "감독·제재" in configured:
            adjusted["감독·제재"] -= 2.0

    if has_policy:
        if "입법·정책" in configured:
            adjusted["입법·정책"] += 4.0
        if "거시·시장" in configured:
            adjusted["거시·시장"] -= 2.0
        if "감독·제재" in configured:
            adjusted["감독·제재"] -= 3.0 if not has_action else 1.0

    if "감독·제재" in configured:
        if has_regulator and has_action and not has_policy:
            adjusted["감독·제재"] += 8.0
        elif has_regulator and not has_action:
            adjusted["감독·제재"] -= 4.0
        elif not has_regulator:
            adjusted["감독·제재"] -= 10.0

    return adjusted


def _apply_title_biases(
    title_text: str,
    desc_text: str,
    title_scores: dict[str, int],
) -> dict[str, float]:
    adjusted = {k: float(v) for k, v in title_scores.items()}
    configured = set(adjusted)
    combined = f"{title_text} {desc_text}".strip()
    has_schedule = _is_schedule_article(combined)
    has_action = _has_supervisory_action(combined)
    has_regulator = _has_regulator_anchor(combined)
    has_policy = _has_policy_signal(combined)
    has_market_context = _has_market_context(combined)
    has_generic_macro = _has_generic_macro_term(combined)
    has_corporate_context = _has_corporate_earnings_context(combined)
    has_financial_context = _has_financial_company_context(combined)
    has_bank_title = _has_bank_identity(title_text)
    has_bank_desc = _has_bank_identity(desc_text)
    has_market_title = _has_market_title_signal(title_text)
    has_bank_quote_source = _has_bank_quote_source_signal(combined)
    has_explicit_bank_title = _has_explicit_bank_brand(title_text)
    has_bank_subject_title = has_bank_title and (has_explicit_bank_title or not (has_market_title and _has_bank_quote_source_signal(title_text)))

    if "은행" in configured:
        if has_bank_subject_title:
            adjusted["은행"] += 7.0
        elif has_bank_desc:
            adjusted["은행"] += 1.0
        if has_bank_quote_source:
            adjusted["은행"] -= 2.0
    if has_market_title and "거시·시장" in configured:
        adjusted["거시·시장"] += 6.0
        if "은행" in configured and (has_bank_quote_source or has_bank_desc) and not has_bank_subject_title:
            adjusted["은행"] -= 3.0
    if "거시·시장" in configured and has_generic_macro and not has_market_context:
        if has_corporate_context and not has_financial_context:
            adjusted["거시·시장"] -= 12.0
        elif has_financial_context:
            adjusted["거시·시장"] -= 9.0
        else:
            adjusted["거시·시장"] -= 7.0
    if has_schedule:
        for sector in ("감독·제재", "입법·정책", "거시·시장"):
            if sector in configured:
                adjusted[sector] -= 1.0
        if not has_action and "감독·제재" in configured:
            adjusted["감독·제재"] -= 2.0
    if has_policy:
        if "입법·정책" in configured:
            adjusted["입법·정책"] += 3.0
        if "거시·시장" in configured:
            adjusted["거시·시장"] -= 1.0
        if "감독·제재" in configured:
            adjusted["감독·제재"] -= 3.0 if not has_action else 1.0
    if "감독·제재" in configured:
        if has_regulator and has_action and not has_policy:
            adjusted["감독·제재"] += 12.0
        elif has_regulator and not has_action:
            adjusted["감독·제재"] -= 5.0
        elif not has_regulator:
            adjusted["감독·제재"] -= 12.0
    return adjusted


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
    sector: str,
    topic: str,
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

    context = TOPIC_CONTEXT_TOKENS.get(topic, {})
    title_context_hits = _collect_hits(list(context.get("title", ())), title_text)
    body_context_hits = _collect_hits(list(context.get("body", ())), body_text)
    has_sector_affinity = sector in TOPIC_SECTOR_AFFINITY.get(topic, ())

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
        + len([kw for kw in title_context_hits if kw not in title_strong_hits and kw not in title_weak_hits])
        * TOPIC_TITLE_CONTEXT_SCORE
        + len([kw for kw in body_context_hits if kw not in body_strong_hits and kw not in body_weak_hits])
        * TOPIC_BODY_CONTEXT_SCORE
        + (TOPIC_SECTOR_AUX_SCORE if has_sector_affinity and (title_strong_hits or title_weak_hits or title_context_hits) else 0.0)
        + len(query_hits) * TOPIC_QUERY_AUX_SCORE
        - len(title_negative_hits) * TOPIC_TITLE_NEGATIVE_PENALTY
        - len(body_negative_hits) * TOPIC_BODY_NEGATIVE_PENALTY
    )
    hits = _unique_keep_order(
        [*title_strong_hits, *title_weak_hits, *body_strong_hits, *body_weak_hits, *title_context_hits, *body_context_hits]
    )
    return score, hits


def _keyword_in_text(keyword: str, text: str) -> bool:
    kw = (keyword or "").strip()
    if not kw:
        return False
    if kw.lower() == "리스":
        return contains_term(text, kw, exclude_terms=["리스크"], mode="phrase")
    if kw in RISKY_SHORT_KEYWORDS or kw.lower() in {"은행", "거래소", "펀드", "대출", "보험"}:
        return contains_term(text, kw, mode="token")
    return contains_term(text, kw)


def _collect_hits(keywords: list[str], text: str) -> list[str]:
    return [kw for kw in keywords if _keyword_in_text(kw, text)]


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
        sector_scores: dict[str, int] = {}
        sector_title_scores: dict[str, int] = {}
        sector_hits: dict[str, list[str]] = {}
        for sector, keywords in sector_queries.items():
            rules = _build_sector_rules(sector, keywords)
            score, title_score, hits = _score_sector(title_text, desc_text, rules)
            sector_scores[sector] = score
            sector_title_scores[sector] = title_score
            sector_hits[sector] = hits

        adjusted_scores = _apply_sector_adjustments(title_text, desc_text, sector_scores)
        adjusted_title_scores = _apply_title_biases(title_text, desc_text, sector_title_scores)
        for sector, score in adjusted_scores.items():
            title_score = adjusted_title_scores.get(sector, 0.0)
            if (title_score, score) > (best_title_score, best_score):
                best_score = score
                best_title_score = title_score
                best_sector = sector
                best_hits = sector_hits.get(sector, [])

        sectors = [best_sector] if best_score >= PRIMARY_SECTOR_THRESHOLD else ["기타"]

        # -----------------------
        # Topics: multi
        # -----------------------
        topics: list[str] = []
        topic_hits_all: list[str] = []
        for topic, keywords in topic_queries.items():
            rules = _build_topic_rules(topic, keywords)
            score, hits = _score_topic(title_text, body_text, query_text, best_sector, topic, rules)
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
