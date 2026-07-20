from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from src.pipeline.filtering import is_blocked_source_url
from src.pipeline.source_quality import publisher_name
from src.pipeline.tagger import TaggedArticle

_BRACKET_LABEL_RE = re.compile(r"\[[^\]]*(?:속보|단독|종합|사진|영상)[^\]]*\]|\([^)]*(?:종합|사진|영상)[^)]*\)")
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:분기|월|일|년|조|억|만|%|bp|兆|億)?")

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

_CROSS_SECTOR_SAFE_PREFIXES = ("finance:securities_liquidity", "finance:loan_relief")


def _article_field(article: Any, key: str) -> Any:
    if isinstance(article, dict):
        return article.get(key)
    return getattr(article, key, None)


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
    if (_contains_any(text, ("새도약기금", "장기연체채권")) and _contains_any(text, ("대부업권", "대부업계", "대부업체"))) or _contains_any(text, ("상품권 사채", "불법사금융", "내구제대출")):
        return "finance:loan_relief"
    return None



def _important_issue_terms(text: str) -> set[str]:
    normalized = _normalize_issue_text(text)
    terms: set[str] = set()
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

def _issue_fingerprint(item: TaggedArticle) -> str | None:
    text = _normalize_issue_text(f"{_article_title(item)} {_article_field(item.article, 'description') or ''}")
    return _rule_issue_fingerprint(item) or _digital_asset_fingerprint(text) or _macro_market_fingerprint(text) or _finance_policy_fingerprint(text)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _meaningful_overlap(a_tokens: set[str], b_tokens: set[str]) -> bool:
    shared = (a_tokens & b_tokens) - _GENERIC_TOKENS - _STOPWORDS
    return len(shared) >= 2 or bool(shared & (_extract_entities(" ".join(a_tokens)) | _extract_entities(" ".join(b_tokens))))


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
    )


def _should_cluster_features(a: _ClusterFeatures, b: _ClusterFeatures) -> bool:
    if not a.norm_title or not b.norm_title:
        return False
    if a.norm_title == b.norm_title:
        return True

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


def cluster_tagged_articles(tagged: list[TaggedArticle]) -> list[TaggedArticle]:
    features = [_build_cluster_features(item) for item in tagged]
    index_clusters: list[list[int]] = []
    for idx in range(len(tagged)):
        target: list[int] | None = None
        for cluster in index_clusters:
            if any(_should_cluster_features(features[idx], features[member]) for member in cluster):
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
            setattr(item.article, "cluster_id", cid)
            setattr(item.article, "cluster_size", size)
            setattr(item.article, "cluster_rank", rank)
            setattr(item.article, "cluster_is_representative", item is representative)
            setattr(item.article, "related_count", max(size - 1, 0))
            setattr(item.article, "related_articles", [])

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

        setattr(representative.article, "related_articles", related[:5])
        representatives.append(representative)

    return representatives
