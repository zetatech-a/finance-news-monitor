from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from src.pipeline.fields import field_value as _article_field
from src.pipeline.filtering import is_blocked_source_url
from src.pipeline.source_quality import publisher_name
from src.pipeline.tagger import TaggedArticle
from src.pipeline.text_matcher import has_any_term

_BRACKET_LABEL_RE = re.compile(r"\[[^\]]*(?:속보|단독|종합|사진|영상)[^\]]*\]|\([^)]*(?:종합|사진|영상)[^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:분기|월|일|년|조|억|만|%|bp|兆|億)?")
# A bare Korean word ending in 시 is not evidence of a place (e.g. 필요시).
# Recognize metropolitan names and explicit city-hall/police agency suffixes;
# unrecognized abbreviated city names fall back to ordinary title similarity.
_LOCAL_AUTHORITY_RE = re.compile(
    r"(?<![가-힣])(?:서울(?:특별)?시|(?:부산|대구|인천|광주|대전|울산)(?:광역)?시|"
    r"세종(?:특별자치)?시|[가-힣]{2,8}(?:시청|경찰청))(?=[^가-힣]|[은는이가의에]|$)"
)
# Shared label vocabulary for issue evidence, occurrence parsing and identity.
_CAPITAL_ADEQUACY_LABEL = r"(?:(?:킥스|k[- ]?ics)(?:\s*비율)?|지급여력\s*비율)"
_CAPITAL_ADEQUACY_LABEL_RE = re.compile(_CAPITAL_ADEQUACY_LABEL, re.IGNORECASE)
_CAPITAL_ADEQUACY_ISSUE_RE = re.compile(
    r"(?<![가-힣a-z0-9])" + _CAPITAL_ADEQUACY_LABEL
    + r"(?=[은는이가의도과와을를만]?(?![가-힣a-z0-9]))", re.IGNORECASE,
)
_METRIC_OCCURRENCE_PATTERN = (
    r"(?<![가-힣a-z0-9])(" + _CAPITAL_ADEQUACY_LABEL + r"|연체율|예대\s*금리차)"
    # Complete optional particle followed by a numeric percentage; 도입/만기 fail.
    r"\s*(?:은|는|이|가|도|만)?\s*(\d+(?:\.\d+)?)\s*%"
)
# Level and percentage-point reports share identity/subject/period evidence.
_METRIC_OCCURRENCE_RE = re.compile(_METRIC_OCCURRENCE_PATTERN, re.IGNORECASE)
_REPORTED_METRIC_RE = re.compile(
    _METRIC_OCCURRENCE_PATTERN + r"(?!\s*(?:p|포인트))", re.IGNORECASE,
)

_ALIASES: tuple[tuple[str, str], ...] = (
    ("카뱅", "카카오뱅크"),
    ("금감원", "금융감독원"),
    ("금융위", "금융위원회"),
    ("순익", "순이익"),
    ("원달러", "원 달러"),
    ("미국채", "미 국채"),
)

_STOPWORDS = {
    "기자", "단독", "속보", "종합", "오늘", "내일", "올해", "작년", "사진", "영상", "전망", "발표", "관련", "대상",
}
_GENERIC_TOKENS = {
    "금융위", "금융위원회", "금감원", "금융감독원", "금융당국", "은행", "은행권", "금융권", "금리", "대출", "환율", "증시",
    "시장", "금융시장", "경제", "정책", "발표", "검사", "제재", "일정", "브리핑", "사회공헌", "캠페인", "행사", "연체",
    "실적", "영업이익", "순이익", "증가", "감소", "상승", "하락", "확대", "부담", "최대", "역대", "마감", "착수", "경고등",
}

_DISTINCTIVE_TERM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("savings_bank", ("저축은행",)),
    ("mg_community_credit", ("새마을금고",)),
    ("credit_union", ("신협", "신협중앙회")),
    ("card_loan", ("카드론",)),
    ("illegal_collection", ("불법추심", "불법 추심")),
    ("illegal_private_lending", ("불법사금융", "불법 사금융", "불법대부", "불법 대부")),
    ("voice_phishing", ("보이스피싱",)),
    ("loan_ad", ("대출광고", "대출 광고", "대부광고", "대부 광고")),
    ("delinquency_rate", ("연체율",)),
    ("bad_loan", ("부실채권", "부실 채권")),
    ("real_estate_pf", ("부동산 pf", "부동산pf")),
    ("pf", ("pf",)),
    ("exposure", ("익스포저",)),
    ("workout", ("워크아웃",)),
    ("deposit_rate", ("예금금리", "예금 금리", "수신금리", "수신 금리")),
    ("loan_deposit_spread", ("예대금리차", "예대 금리차")),
    ("minus_account", ("마이너스통장", "마이너스 통장")),
    ("household_loan", ("가계대출", "가계 대출")),
    ("mortgage", ("주담대", "주택담보대출", "주택담보 대출")),
    ("dsr", ("dsr",)),
    ("ltv", ("ltv",)),
    ("mis_selling", ("불완전판매", "불완전 판매")),
    ("field_inspection", ("현장점검", "현장 점검", "현장검사", "현장 검사", "검사")),
    ("administrative_action", ("행정처분", "행정 처분")),
    ("penalty_surcharge", ("과징금",)),
    ("liquidity", ("유동성",)),
    ("credit_finance_bond", ("여전채",)),
    ("corporate_bond", ("회사채",)),
    ("treasury_yield", ("국채금리", "국채 금리")),
    ("usdkrw", ("원달러", "원 달러", "원/달러")),
    ("pce", ("pce",)),
    ("cpi", ("cpi",)),
    ("fomc", ("fomc",)),
    ("fed", ("연준",)),
    ("sns", ("sns",)),
    ("threat", ("협박",)),
    ("crackdown", ("단속", "특별단속")),
    ("four_percent", ("4%", "4 %", "4퍼센트", "4프로")),
    ("insurance", ("보험사", "보험")),
    ("kospi", ("코스피",)),
)

_LOW_VALUE_TERMS = (
    "다음주", "이번주", "일정", "주요일정", "금융 브리핑", "오늘의 은행", "금융권 소식", "단신", "사회공헌",
    "캠페인", "행사", "기부", "후원", "mou", "업무협약", "칼럼", "사설", "기고", "기자수첩", "시론",
)

_ENTITY_PATTERNS = (
    "카카오뱅크", "케이뱅크", "토스뱅크", "국민은행", "신한은행", "우리은행", "하나은행", "농협은행", "기업은행",
    "산업은행", "수출입은행", "금융감독원", "금융위원회", "한국은행", "국민연금", "예금보험공사", "신용보증기금", "기술보증기금",
)
_ENTITY_SUFFIXES = ("은행", "증권", "보험", "카드", "캐피탈", "저축은행", "자산운용", "거래소")

_CROSS_SECTOR_SAFE_PREFIXES = ("finance:securities_liquidity", "finance:delinquent_debt_purchase")


def _article_title(item: TaggedArticle) -> str:
    return str(_article_field(item.article, "title") or "")


def _primary_sector(item: TaggedArticle) -> str:
    return item.sectors[0] if item.sectors else "기타"


def _normalize_title(title: str) -> str:
    text = html.unescape(title or "").lower()
    text = _TAG_RE.sub(" ", text)
    text = _BRACKET_LABEL_RE.sub(" ", text)
    for src, dst in _ALIASES:
        text = text.replace(src.lower(), dst.lower())
    text = re.sub(r"[‘’\'\"“”·…,:;!?/\\|_+=~`<>{}]", " ", text)
    text = re.sub(r"[-–—]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_issue_text(text: str) -> str:
    return _normalize_title(text)


def _tokenize_title(title: str) -> set[str]:
    normalized = _normalize_title(title)
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(normalized)}
    return {t for t in tokens if len(t) >= 2 and t not in _STOPWORDS}


def _extract_numbers(title: str) -> set[str]:
    normalized = _normalize_title(title)
    numbers: set[str] = set()
    for match in _NUMBER_RE.finditer(normalized):
        value = re.sub(r"\s+", "", match.group(0).replace(",", ""))
        if value:
            numbers.add(value)
    return numbers


def _extract_entities(title: str) -> set[str]:
    normalized = _normalize_title(title)
    entities = {entity for entity in _ENTITY_PATTERNS if entity.lower() in normalized}
    for token in _tokenize_title(normalized):
        if token in _GENERIC_TOKENS:
            continue
        if any(token.endswith(suffix) for suffix in _ENTITY_SUFFIXES) and token not in {"은행권", "금융권"}:
            entities.add(token)
    return entities


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _macro_market_fingerprint(text: str) -> str | None:
    has_bok = _contains_any(text, ("금통위", "금융통화위원회", "한국은행", "한은", "통화정책방향"))
    has_rate_decision = _contains_any(text, ("금리 결정", "기준금리 결정", "금리 동결", "금리 인하", "금리 인상", "통화정책방향"))
    if (has_bok and "기준금리" in text) or ("기준금리" in text and has_rate_decision):
        return "macro:bok_base_rate"
    if _contains_any(text, ("bmsi", "채권심리", "채권시장 심리", "채권시장지표", "금리전망 bmsi")):
        return "macro:bond_sentiment_bmsi"
    if _contains_any(text, ("fomc", "점도표", "파월")) or ("연준" in text and _contains_any(text, ("금리 경로", "통화정책", "fomc"))):
        return "macro:fomc"
    if _contains_any(text, ("cpi", "pce", "물가지표", "인플레이션", "소비자물가", "개인소비지출")):
        return "macro:inflation_data"
    if _contains_any(text, ("미 국채금리", "미국채 금리", "미국 10년물", "미 10년물", "글로벌 채권금리", "국채금리 급등", "국채 금리 급등")) or ("미 국채" in text and "금리" in text):
        return "macro:ust_yield"
    if _contains_any(text, ("원 달러", "원/달러", "달러 강세", "외환시장", "환율 급등", "원화 약세")):
        return "macro:fx_usdkrw"
    return None


def _digital_asset_fingerprint(text: str) -> str | None:
    platform_by_term = {
        "업비트": "upbit",
        "두나무": "dunamu",
        "빗썸": "bithumb",
        "코빗": "korbit",
        "고팍스": "gopax",
    }
    issue_type_terms = {
        "deposit": ("예치금",),
        "fee": ("수수료",),
        "volume": ("거래량",),
        "investment": ("지분투자",),
        "partnership": ("제휴",),
        "listing": ("상장",),
        "hacking": ("해킹",),
        "abnormal_trade": ("이상거래",),
        "sanction": ("제재",),
    }
    digital_context = ("fiu", "금융정보분석원", "가상자산", "가상자산거래소", "코인거래소", "업비트", "빗썸", "코빗", "고팍스", "두나무", "암호화폐", "디지털자산")
    sanction_context = ("제재", "제재심", "영업정지", "과태료", "검사", "신고", "적발", "가상자산거래소 제재")

    has_platform = any(term in text for term in platform_by_term)
    if _contains_any(text, digital_context) and _contains_any(text, sanction_context):
        return "digital:fiu_sanction"
    if _contains_any(text, ("토큰증권", "sto", "조각투자", "증권형 토큰", "제도화", "법제화")):
        return "digital:sto"
    if has_platform and _contains_any(text, ("스테이블코인", "원화 스테이블코인")):
        return "digital:platform_stablecoin"
    if has_platform:
        platform = next((alias for term, alias in platform_by_term.items() if term in text), "platform")
        issue_type = next((name for name, terms in issue_type_terms.items() if _contains_any(text, terms)), None)
        if issue_type:
            return f"digital:{platform}:{issue_type}"
    if _contains_any(text, ("비트코인", "이더리움", "암호화폐", "가상자산 시세")) and _contains_any(text, ("급등", "급락", "신고가", "조정", "랠리")):
        return "digital:price_action"
    return None


def _finance_policy_fingerprint(text: str) -> str | None:
    has_specific_anchor = _contains_any(text, ("금융투자업규정", "신조정유동성비율", "조정유동성비율", "레고랜드 사태"))
    has_securities_liquidity_context = ("증권사" in text) and _contains_any(
        text,
        ("abcp", "유동성", "유동성비율", "ncr", "순자본비율", "유동성 규제", "규제 확대"),
    )
    if has_specific_anchor or has_securities_liquidity_context:
        return "finance:securities_liquidity"
    if _contains_any(text, ("여전채", "카드채", "캐피탈채")) and _contains_any(text, ("조달", "조달금리", "만기", "차환", "부담", "금리")):
        return "finance:credit_funding"
    # A named debt-relief programme + transaction/participation is an issue.
    # Illegal lending and loan-business sector words alone are only domains.
    if (
        _contains_any(text, ("새도약기금", "장기연체채권"))
        and _contains_any(text, ("매입", "매각", "소각", "참여", "협상"))
        and _contains_any(text, ("대부업권", "대부업계", "대부업체"))
    ):
        return "finance:delinquent_debt_purchase"
    return None



def _important_issue_terms(text: str) -> set[str]:
    normalized = _normalize_issue_text(text)
    terms: set[str] = set()
    if _CAPITAL_ADEQUACY_ISSUE_RE.search(normalized):
        terms.add("capital_adequacy_ratio")
    for canonical, aliases in _DISTINCTIVE_TERM_ALIASES:
        if any(alias.lower() in normalized for alias in aliases):
            terms.add(canonical)
    for token in _tokenize_title(normalized):
        if token not in _GENERIC_TOKENS and len(token) >= 3:
            terms.add(token)
    return terms


def _extract_issue_terms(item: TaggedArticle) -> set[str]:
    text = f"{_article_title(item)} {_article_field(item.article, 'description') or ''}"
    return _important_issue_terms(text)


def _is_low_value_format(text: str) -> bool:
    normalized = _normalize_issue_text(text)
    return _contains_any(normalized, _LOW_VALUE_TERMS)


def _low_value_named_terms(text: str) -> set[str]:
    normalized = _normalize_issue_text(text)
    low_value_generic_terms = {"sns", "threat", "crackdown"}
    return _extract_entities(normalized) | {
        term for term in _important_issue_terms(normalized) if term not in low_value_generic_terms
    }


def _rule_issue_fingerprint(item: TaggedArticle) -> str | None:
    terms = _extract_issue_terms(item)
    if {"savings_bank", "field_inspection"}.issubset(terms):
        return "rule:savings_bank_inspection"
    if {"illegal_collection", "sns"}.issubset(terms) or {"illegal_collection", "threat"}.issubset(terms):
        return "rule:illegal_collection_sns_threat"
    if {"savings_bank", "deposit_rate"}.issubset(terms) and "four_percent" in terms:
        return "rule:savings_bank_deposit_rate_4pct"
    if ({"illegal_private_lending", "loan_ad"} & terms) and "crackdown" in terms:
        if "loan_ad" in terms or "sns" in terms:
            return "rule:illegal_loan_ad_crackdown"
    if {"card_loan", "delinquency_rate"}.issubset(terms):
        return "rule:card_loan_delinquency_rate"
    return None


def _is_enforcement_headline(title: str) -> bool:
    return ((_contains_any(title, ("불법사금융", "불법 사금융", "불법대부", "불법 대부"))
             or has_any_term(title, ("미등록대부", "불법사채")))
            and bool(re.search(
                # Complete action uses, including established action compounds;
                # agency nouns (수사기관/단속기관) are not headline event evidence.
                r"(?<![가-힣a-z0-9])(?:(?:집중|특별|합동|보완|인지)?(?:단속|수사)"
                r"(?:[은는이가의을를에]|해|한다|했다|개시|의뢰)?|잡는다)(?![가-힣a-z0-9])",
                title, re.IGNORECASE,
            )))


def _canonical_local_authority(authority: str) -> str:
    if authority.endswith("경찰청"):
        jurisdiction = authority.removesuffix("경찰청")
        # Normalize only metropolitan jurisdiction spelling, preserving agency.
        jurisdiction = re.sub(r"(?:특별자치시|특별시|광역시|시)$", "", jurisdiction)
        return jurisdiction + "경찰청"
    if authority.endswith(("시", "시청")):
        return authority.replace("특별자치", "").replace("특별", "").replace("광역", "").removesuffix("청")
    return authority


def _targeted_enforcement_fingerprint(title: str, text: str) -> str | None:
    """Local enforcement needs an actor and a specific target, not just a domain.

    This also joins wire variants with different headline wording. Ambiguous
    multi-authority/target roundups intentionally receive no shortcut.
    """
    if not _is_enforcement_headline(title):
        return None
    # The snippet may fill a missing actor/target, but cannot supply the event.
    authorities = {
        _canonical_local_authority(authority)
        for authority in _LOCAL_AUTHORITY_RE.findall(text)
    }
    targets = {
        target for target, aliases in (
            ("small_business", ("전통시장", "소상공인")),
            ("youth", ("청소년",)), ("military", ("군인",)),
            ("university", ("대학생",)),
        ) if _contains_any(text, aliases)
    }
    if len(authorities) == len(targets) == 1:
        return f"enforcement:{next(iter(authorities))}:{next(iter(targets))}"
    return None


def _issue_fingerprint(item: TaggedArticle) -> str | None:
    text = _normalize_issue_text(f"{_article_title(item)} {_article_field(item.article, 'description') or ''}")
    finance = _finance_policy_fingerprint(text)
    if finance == "finance:delinquent_debt_purchase" and not _contains_any(
        _normalize_issue_text(_article_title(item)), ("새도약기금", "장기연체채권"),
    ):
        # A background reference in a policy/crime snippet is not its main event.
        finance = None
    return _targeted_enforcement_fingerprint(_normalize_issue_text(_article_title(item)), text) or _rule_issue_fingerprint(item) or _digital_asset_fingerprint(text) or _macro_market_fingerprint(text) or finance


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _meaningful_overlap(a_tokens: set[str], b_tokens: set[str]) -> bool:
    shared = (a_tokens & b_tokens) - _GENERIC_TOKENS - _STOPWORDS
    return len(shared) >= 2 or bool(shared & (_extract_entities(" ".join(a_tokens)) | _extract_entities(" ".join(b_tokens))))


def _canonical_metric_value(value: str) -> str:
    # The metric regex accepts only unsigned decimal digits; no float rounding.
    whole, _, fraction = value.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return whole + ("." + fraction if fraction else "")


def _metric_identity(label: str) -> str | None:
    if _CAPITAL_ADEQUACY_LABEL_RE.fullmatch(label):
        return "capital_adequacy_ratio"
    return next(iter(_important_issue_terms(label) & {
        "capital_adequacy_ratio", "delinquency_rate", "loan_deposit_spread",
    }), None)


def _metric_identities(title: str) -> set[str]:
    return {metric for label, _ in _METRIC_OCCURRENCE_RE.findall(title)
            if (metric := _metric_identity(label)) is not None}


def _reported_metrics(title: str) -> set[tuple[str, str]]:
    facts = set()
    for label, value in _REPORTED_METRIC_RE.findall(title):
        metric = _metric_identity(label)
        if metric:
            facts.add((metric, _canonical_metric_value(value)))
    return facts


# Exact company identities observed in stored candidates (NH: August 7).
# Local to metrics: a suffix-wide 손보 replacement would equate unverified names.
_METRIC_SUBJECT_ALIASES = {
    "kb손보": "kb손해보험", "db손보": "db손해보험", "nh농협손보": "nh농협손해보험",
    "교보생명보험": "교보생명",
}
# Aggregate synonyms only; life/non-life, banks/savings banks remain distinct.
_METRIC_INDUSTRY_SUBJECT_ALIASES = {
    "보험회사": "보험사", "생명보험": "생보사", "손해보험": "손보사",
}


def _canonical_metric_subject(subject: str) -> str:
    # Exact generic labels only: 삼성생명/KB손해보험 must stay named companies.
    return _METRIC_INDUSTRY_SUBJECT_ALIASES.get(
        subject, _METRIC_SUBJECT_ALIASES.get(subject, subject),
    )


# Metric-local contract: complete particles, not prefixes of words like 이익.
_METRIC_SUBJECT_END = r"(?=[은는이가의도과와을를]?(?![가-힣a-z0-9]))"


def _metric_subjects(title: str) -> set[str]:
    """Positive headline evidence of the measured entity, not its regulator.

    Keep this local to reported metrics: changing general entity extraction
    would alter unrelated earnings/market clustering. Missing subjects never
    authorize the measurement shortcut.
    """
    measurements = list(_METRIC_OCCURRENCE_RE.finditer(title))
    # All measured values cannot be attributed to the first subject. Keep
    # ordinary similarity available, but authorize no metric shortcut/veto for
    # multi-measurement headlines (even repeated equal values after rounding).
    if len(measurements) != 1:
        return set()
    measurement = measurements[0]
    # The subject precedes the measurement. Later comparisons may mention
    # other companies or subsectors and must not redefine whose value it is.
    title = title[:measurement.start()]
    # An explicit aggregate immediately governing the measurement owns it;
    # example companies before "등/포함한" do not. "보험사 중 삼성생명" and
    # bare company lists deliberately do not match this construction.
    aggregate = re.search(
        r"(?<![가-힣a-z0-9])(?:등|포함한|포함)\s+(?:[1-9][0-9]*개\s+)?"
        r"(보험사|보험회사|생보사|손보사|은행권|저축은행권|카드사)"
        r"(?:들)?[은는이가의]?\s*$", title,
    )
    if aggregate:
        return {_canonical_metric_subject(aggregate.group(1))}
    excluded_regulators = {"금융감독원", "금융위원회", "한국은행"}
    subjects = {
        entity for entity in _extract_entities(title) - excluded_regulators
        if re.search(r"(?<![가-힣a-z0-9])" + re.escape(entity) + _METRIC_SUBJECT_END, title)
    }
    subjects.update(re.findall(
        r"(?<![가-힣a-z0-9])[가-힣a-z0-9]+(?:생명|손보|화재|라이프|보험|은행|카드|캐피탈)"
        + _METRIC_SUBJECT_END, title,
    ))
    # The generic company-suffix path must not reintroduce excluded regulators.
    subjects.difference_update(excluded_regulators)
    if subjects:
        return {_canonical_metric_subject(subject) for subject in subjects}
    # Explicit industry-wide statistics have subjects too. Do not infer these
    # merely from the sector tag or a background snippet.
    # Accept complete particles/plural suffixes, not lexical continuations
    # such as 은행권이익 or 저축은행권역.
    industry_subjects = re.findall(
        r"(?<![가-힣])(?:보험사|보험회사|생보사|손보사|은행권|저축은행권|카드사)"
        r"(?=(?:들)?[은는이가의]?(?![가-힣a-z0-9]))", title,
    )
    return {_canonical_metric_subject(subject) for subject in industry_subjects}


def _metric_period(title: str) -> tuple[int | None, int | None]:
    """Explicit year/as-of month preceding the first reported metric only.

    Quarter/half-year labels describe the same ratio snapshot as their end
    month (2분기 == 상반기 == 6월말). Missing or ambiguous dimensions stay unknown;
    relative years and later background comparisons do not establish a period.
    """
    measurement = _METRIC_OCCURRENCE_RE.search(title)
    if not measurement:
        return None, None
    prefix = title[:measurement.start()]
    # Only an adjacent, complete comparison marker governs a baseline date.
    # Mask that date expression locally, preserving other current snapshots.
    subyear = r"(?:[1-4]분기(?:말)?|[상하]반기|(?:1[0-2]|[1-9])월(?:\s*말)?)"
    prefix = re.sub(
        r"(?<![가-힣a-z0-9])(?:(?:19|20)[0-9]{2}년(?:\s*" + subyear + r")?|"
        + subyear + r")\s*(?:대비|보다)(?![가-힣a-z0-9])", " ", prefix,
    )
    years = {int(year) for year in re.findall(r"(?<![0-9])((?:19|20)[0-9]{2})년", prefix)}
    months = {int(quarter) * 3 for quarter in re.findall(r"(?<![0-9])([1-4])분기", prefix)}
    months.update(6 if half == "상" else 12 for half in re.findall(r"([상하])반기", prefix))
    months.update(int(month) for month in re.findall(r"(?<![0-9])(1[0-2]|[1-9])월\s*말", prefix))
    # Bare months are explicit snapshots too, but 월물/월호 and out-of-range
    # numbers are not. 월말 remains handled above, without a second bare match.
    months.update(int(month) for month in re.findall(
        r"(?<![0-9])(1[0-2]|[1-9])월(?![가-힣a-z0-9])", prefix,
    ))
    return (next(iter(years)) if len(years) == 1 else None,
            next(iter(months)) if len(months) == 1 else None)


def _enforcement_period(title: str) -> tuple[int | None, int | None]:
    """Explicit headline year/month; missing or ambiguous dimensions stay unknown."""
    if not _is_enforcement_headline(title):
        return None, None
    years = {int(year) for year in re.findall(r"(?<![0-9])((?:19|20)[0-9]{2})년", title)}
    months = {int(month) for month in re.findall(r"(?<![0-9])(1[0-2]|[1-9])월", title)}
    return (next(iter(years)) if len(years) == 1 else None,
            next(iter(months)) if len(months) == 1 else None)


@dataclass
class _ClusterFeatures:
    """페어 비교(O(n²))마다 재계산하지 않도록 기사당 1회만 뽑아두는 피처."""

    norm_title: str
    sector: str
    fingerprint: str | None
    low_value: bool
    low_value_named_terms: set[str]
    tokens: set[str]
    entities: set[str]
    numbers: set[str]
    issue_terms: set[str]
    metric_identities: set[str]
    reported_metrics: set[tuple[str, str]]
    metric_subjects: set[str]
    metric_period: tuple[int | None, int | None]
    enforcement_period: tuple[int | None, int | None]


def _build_cluster_features(item: TaggedArticle) -> _ClusterFeatures:
    title = _article_title(item)
    norm_title = _normalize_title(title)
    return _ClusterFeatures(
        norm_title=norm_title,
        sector=_primary_sector(item),
        fingerprint=_issue_fingerprint(item),
        low_value=_is_low_value_format(norm_title),
        low_value_named_terms=_low_value_named_terms(norm_title),
        tokens=_tokenize_title(title),
        entities=_extract_entities(title),
        numbers=_extract_numbers(title),
        issue_terms=_extract_issue_terms(item),
        metric_identities=_metric_identities(norm_title),
        reported_metrics=_reported_metrics(norm_title),
        metric_subjects=_metric_subjects(norm_title),
        metric_period=_metric_period(norm_title),
        enforcement_period=_enforcement_period(norm_title),
    )


def _conflicting_metric_subjects(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    return bool(
        a.metric_identities & b.metric_identities
        and a.metric_subjects and b.metric_subjects and not (a.metric_subjects & b.metric_subjects)
    )


def _conflicting_metric_periods(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    # Recurring reports can change value. Period safety needs the same metric
    # identity; only the exact-match shortcut below also requires equal values.
    shared_metrics = a.metric_identities & b.metric_identities
    if not (shared_metrics
            and len(a.metric_subjects) == 1 and a.metric_subjects == b.metric_subjects):
        return False
    return any(left is not None and right is not None and left != right
               for left, right in zip(a.metric_period, b.metric_period))


def _conflicting_enforcement_periods(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    # Keep the family fingerprint stable for wires omitting the year. Only
    # explicit conflicting periods of the same authority/target veto a pair.
    return bool(
        (a.fingerprint or "").startswith("enforcement:")
        and a.fingerprint == b.fingerprint
        and any(left is not None and right is not None and left != right
                for left, right in zip(a.enforcement_period, b.enforcement_period))
    )


def _metric_event_text(feature: _ClusterFeatures) -> str:
    """Ordered residual evidence; removed facts remain adjacency barriers."""
    text = _METRIC_OCCURRENCE_RE.sub(" | ", feature.norm_title)
    names = feature.metric_subjects | {
        alias for alias in _METRIC_SUBJECT_ALIASES | _METRIC_INDUSTRY_SUBJECT_ALIASES
        if _canonical_metric_subject(alias) in feature.metric_subjects
    }
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(r"(?<![가-힣a-z0-9])" + re.escape(name)
                      + r"[은는이가의도과와을를]?(?![가-힣a-z0-9])", " | ", text)
    # A year alone is not an independent event identifier. These are the same
    # explicit period forms understood by _metric_period, not publication dates.
    text = re.sub(r"(?<![0-9])(?:[0-9]{4}년|[1-4]분기(?:말)?|[0-9]{1,2}월\s*말)|[상하]반기", " | ", text)
    return text


def _metric_event_tokens(feature: _ClusterFeatures) -> set[str]:
    """Headline evidence left after removing the already-counted metric fact."""
    return _tokenize_title(_metric_event_text(feature))


def _metric_event_token_variants(token: str) -> set[str]:
    """Comparison-only alternatives; never rewrite global tokens or add evidence."""
    variants = {token}
    # Only the statistical predicates used by this local event comparison.
    predicate = re.fullmatch(r"(하락|상승|감소|증가|개선|확대)(?:했다|한다|됐다|된다)", token)
    if predicate:
        variants.add(predicate.group(1))
    # One complete particle, at least two Hangul syllables in the stem, and
    # the correct consonant/vowel allomorph. No recursive stripping, 도 or 만.
    if len(token) >= 3 and re.fullmatch(r"[가-힣]+", token):
        stem, particle = token[:-1], token[-1]
        has_final_consonant = (ord(stem[-1]) - ord("가")) % 28 != 0
        particles = "은이을과의에" if has_final_consonant else "는가를와의에"
        if particle in particles:
            variants.add(stem)
    return variants


def _metric_event_comparison_units(feature: _ClusterFeatures) -> set[str]:
    """Veto-only morphology/spacing alternatives, never ordinary merge evidence."""
    text = _metric_event_text(feature)
    tokens = _tokenize_title(text)
    units = {variant for token in tokens for variant in _metric_event_token_variants(token)}
    ordered = list(_TOKEN_RE.finditer(text))
    for left, right in zip(ordered, ordered[1:]):
        # Retain original adjacency: do not jump across removed facts, punctuation,
        # filtered words or single syllables. Only two complete Hangul words join.
        if (left.group() in tokens and right.group() in tokens
                and re.fullmatch(r"[가-힣]{2,}", left.group())
                and re.fullmatch(r"[가-힣]{2,}", right.group())
                and text[left.end():right.start()].isspace()):
            units.update(_metric_event_token_variants(left.group() + right.group()))
    return units


def _metric_match_lacks_event_evidence(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    if not (a.reported_metrics & b.reported_metrics
            and len(a.metric_subjects) == 1 and a.metric_subjects == b.metric_subjects):
        return False
    # Do not turn a missing period into a conflict against a dated report.
    # Such pairs still have to pass ordinary similarity without the shortcut.
    if a.metric_period[1] is not None or b.metric_period[1] is not None:
        return False
    # Stored search headlines may end mid-word (e.g. 하...). Incomplete
    # event wording cannot establish disjoint events; ordinary rules still apply.
    if any(re.search(r"\.{2,}$", feature.norm_title) for feature in (a, b)):
        return False
    left, right = _metric_event_tokens(a), _metric_event_tokens(b)
    # Bare statistical wire labels still use ordinary title similarity. Once
    # both headlines name an event, the repeated fact cannot replace overlap
    # in that event, including via a bare-statistic bridge in a cluster.
    left_variants = _metric_event_comparison_units(a)
    right_variants = _metric_event_comparison_units(b)
    return bool(left and right) and not (left_variants & right_variants)


def _should_cluster_features(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    if not a.norm_title or not b.norm_title:
        return False
    if a.norm_title == b.norm_title:
        return True

    if any((feature.fingerprint or "").startswith("enforcement:") for feature in (a, b)):
        if not (_is_enforcement_headline(a.norm_title) and _is_enforcement_headline(b.norm_title)):
            return False

    # Explicit subject/period conflicts veto even fingerprint/similarity shortcuts.
    if (_conflicting_metric_subjects(a, b) or _conflicting_metric_periods(a, b)
            or _conflicting_enforcement_periods(a, b)
            or _metric_match_lacks_event_evidence(a, b)):
        return False

    if a.low_value or b.low_value:
        if not (a.low_value and b.low_value):
            return False
        named_overlap = a.low_value_named_terms & b.low_value_named_terms
        sim = SequenceMatcher(None, a.norm_title, b.norm_title).ratio()
        return sim >= 0.88 or (bool(named_overlap) and sim >= 0.72)

    same_sector = a.sector == b.sector
    if a.fingerprint and a.fingerprint == b.fingerprint:
        if same_sector:
            return True
        return a.fingerprint.startswith(_CROSS_SECTOR_SAFE_PREFIXES)
    if a.fingerprint and b.fingerprint and a.fingerprint.split(":", 1)[0] == b.fingerprint.split(":", 1)[0]:
        return False

    sim = SequenceMatcher(None, a.norm_title, b.norm_title).ratio()
    if not same_sector:
        return sim >= 0.90
    if min(len(a.norm_title), len(b.norm_title)) < 12:
        return False

    shared_entities = a.entities & b.entities
    shared_numbers = a.numbers & b.numbers
    shared_issue_terms = a.issue_terms & b.issue_terms
    token_jaccard = _jaccard(
        (a.tokens | a.issue_terms) - _GENERIC_TOKENS,
        (b.tokens | b.issue_terms) - _GENERIC_TOKENS,
    )

    # A rounded level is supporting evidence, not an event identifier. Only
    # a shared explicit as-of month (quarter/half-year end included) authorizes
    # statistical wire matching. A shared year alone is insufficient; missing
    # periods fall through to ordinary similarity instead of becoming conflicts.
    if a.reported_metrics & b.reported_metrics:
        if (len(a.metric_subjects) == 1 and a.metric_subjects == b.metric_subjects
                and a.metric_period[1] is not None
                and a.metric_period[1] == b.metric_period[1]):
            return True

    if not shared_issue_terms and not shared_entities and not shared_numbers:
        return False
    if shared_issue_terms and sim >= 0.72 and _meaningful_overlap(a.tokens | a.issue_terms, b.tokens | b.issue_terms):
        return True
    if len(shared_issue_terms) >= 2 and max(_jaccard(a.issue_terms, b.issue_terms), sim * 0.75) >= 0.42:
        return True
    if token_jaccard >= 0.55 and (shared_entities or shared_numbers or shared_issue_terms):
        return True
    if shared_entities and shared_numbers and shared_issue_terms:
        return True
    return False


def _should_cluster(a: TaggedArticle, b: TaggedArticle) -> bool:
    return _should_cluster_features(_build_cluster_features(a), _build_cluster_features(b))


def _relevance_value(item: TaggedArticle) -> float:
    article = item.article
    for key in ("relevance_score", "score"):
        value = _article_field(article, key)
        if isinstance(value, (int, float)):
            return float(value)
    for key in ("relevance_prob", "prob", "relevance"):
        value = _article_field(article, key)
        if isinstance(value, (int, float)):
            return float(value) * 10.0 if 0.0 <= float(value) <= 1.0 else float(value)
    return 0.0


def _link_value(item: TaggedArticle) -> str:
    article = item.article
    return str(_article_field(article, "naver_link") or _article_field(article, "originallink") or _article_field(article, "link") or "").strip()


def _representative_score(item: TaggedArticle) -> tuple[float, int, int, float, str, str]:
    title = _article_title(item).strip()
    desc = str(_article_field(item.article, "description") or "").strip()
    title_len_score = min(len(title), 90) - max(len(title) - 110, 0)
    metadata_score = (2 if desc else 0) + (1 if _link_value(item) else 0)
    pub_date = _article_field(item.article, "pub_date")
    timestamp = float(pub_date.timestamp()) if hasattr(pub_date, "timestamp") else 0.0
    return (_relevance_value(item), title_len_score, metadata_score, timestamp, _normalize_title(title), _link_value(item))


def _related_metadata(item: TaggedArticle) -> dict[str, str]:
    article = item.article
    # press 필드가 없으면(네이버 API는 언론사명을 주지 않음) 원문 도메인으로 출처 라벨 유도
    return {"title": _article_title(item), "link": _link_value(item), "press": publisher_name(article), "pub_date": str(_article_field(article, "pub_date") or "")}


def _cluster_id(members: list[TaggedArticle]) -> str:
    normalized_titles = sorted(_normalize_title(_article_title(member)) for member in members)
    seed = "|".join(normalized_titles).encode("utf-8")
    return "issue-" + hashlib.sha1(seed).hexdigest()[:12]


def _requires_complete_compatibility(feature: _ClusterFeatures) -> bool:
    """Prevent lending bridges without changing unrelated sectors' admission."""
    # Called once per article, not per pair. Preserve combined issue_terms for
    # ordinary similarity; a background snippet cannot switch admission mode.
    headline_terms = _important_issue_terms(feature.norm_title)
    return (feature.sector == "대부"
            or bool(headline_terms & {"illegal_private_lending", "illegal_collection", "loan_ad"})
            or feature.fingerprint == "finance:delinquent_debt_purchase")


def cluster_tagged_articles(tagged: list[TaggedArticle]) -> list[TaggedArticle]:
    features = [_build_cluster_features(item) for item in tagged]
    index_clusters: list[list[int]] = []
    # Limit changed admission/order semantics to lending. Reordering every
    # sector redistributes existing broad macro fingerprints into larger groups.
    strict = [_requires_complete_compatibility(feature) for feature in features]
    order = list(range(len(tagged)))
    loan_slots = [idx for idx in order if strict[idx]]
    loan_order = sorted(loan_slots, key=lambda idx: (
        features[idx].norm_title, _link_value(tagged[idx]),
        str(_article_field(tagged[idx].article, "description") or ""),
    ))
    for slot, idx in zip(loan_slots, loan_order):
        order[slot] = idx
    for idx in order:
        target: list[int] | None = None
        for cluster in index_clusters:
            # Explicit subject/period conflicts cannot be bypassed through a bridge
            # even in sectors retaining their original single-link behavior.
            if any(_conflicting_metric_subjects(features[idx], features[member])
                   or _conflicting_metric_periods(features[idx], features[member])
                   or _conflicting_enforcement_periods(features[idx], features[member])
                   or _metric_match_lacks_event_evidence(features[idx], features[member]) for member in cluster):
                continue
            compatibility = all if strict[idx] or any(strict[member] for member in cluster) else any
            if compatibility(_should_cluster_features(features[idx], features[member]) for member in cluster):
                target = cluster
                break
        if target is None:
            index_clusters.append([idx])
        else:
            target.append(idx)

    clusters = [[tagged[idx] for idx in cluster] for cluster in index_clusters]

    representatives: list[TaggedArticle] = []
    for cluster in clusters:
        representative = max(cluster, key=_representative_score)
        cid = _cluster_id(cluster)
        non_representatives = [item for item in cluster if item is not representative]

        # dedup 단계에서 같은 제목으로 흡수된 다른 출처 기사까지 포함한 총 규모 —
        # "관련 기사 N건" 배지와 Top-10 랭킹의 cluster_size 가중치가 실제 보도량을 반영한다.
        # 흡수분은 1차/2차 필터를 거치지 않았으므로 차단 도메인(엔터/스포츠) 출처는
        # 개수 집계와 목록 노출 모두에서 제외한다.
        def _eligible_duplicates(member: TaggedArticle) -> list[dict[str, str]]:
            return [
                dup
                for dup in getattr(member.article, "duplicate_sources", None) or []
                if not is_blocked_source_url(str(dup.get("link") or ""))
            ]

        absorbed = sum(len(_eligible_duplicates(item)) for item in cluster)
        size = len(cluster) + absorbed

        for rank, item in enumerate(sorted(cluster, key=_representative_score, reverse=True), start=1):
            item.article.cluster_id = cid
            item.article.cluster_size = size
            item.article.cluster_rank = rank
            item.article.cluster_is_representative = item is representative
            item.article.related_count = max(size - 1, 0)
            item.article.related_articles = []

        # 관련 기사 목록: 클러스터 멤버(대표 제외) 우선, 이어서 각 멤버가 흡수한
        # 중복 출처 순으로 병합. 링크(없으면 제목) 기준으로 중복 제거 후 5건 저장.
        related_entries = [_related_metadata(item) for item in non_representatives]
        for item in (representative, *non_representatives):
            for dup in _eligible_duplicates(item):
                related_entries.append(
                    {
                        "title": str(dup.get("title") or ""),
                        "link": str(dup.get("link") or ""),
                        "press": str(dup.get("press") or ""),
                        "pub_date": str(dup.get("pub_date") or ""),
                    }
                )

        # 같은 기사가 두 번 실리는 것을 막는다. 링크 단독 키는 (테스트 픽스처처럼)
        # 링크가 겹치는 다른 기사까지 지워버리므로 링크+제목 조합으로 판별한다.
        def _entry_key(link: str, title: str) -> str:
            return f"{link}|{title}"

        seen_keys: set[str] = {
            _entry_key(_link_value(representative), _article_title(representative))
        }
        related: list[dict[str, str]] = []
        for entry in related_entries:
            key = _entry_key(str(entry.get("link") or ""), str(entry.get("title") or ""))
            if key == "|" or key in seen_keys:
                continue
            seen_keys.add(key)
            related.append(entry)

        representative.article.related_articles = related[:5]
        representatives.append(representative)

    return representatives
