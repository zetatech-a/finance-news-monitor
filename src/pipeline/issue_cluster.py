from __future__ import annotations

import hashlib
import html
import re
from difflib import SequenceMatcher
from typing import Any

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
)

_STOPWORDS = {
    "기자",
    "단독",
    "속보",
    "종합",
    "오늘",
    "내일",
    "올해",
    "작년",
    "사진",
    "영상",
    "전망",
    "발표",
    "관련",
    "대상",
}

_GENERIC_TOKENS = {
    "금리",
    "환율",
    "연체",
    "실적",
    "금융권",
    "은행권",
    "시장",
    "증시",
    "대출",
    "영업이익",
    "순이익",
    "증가",
    "감소",
    "상승",
    "하락",
    "확대",
    "부담",
    "최대",
    "역대",
}

_ENTITY_PATTERNS = (
    "카카오뱅크",
    "케이뱅크",
    "토스뱅크",
    "국민은행",
    "신한은행",
    "우리은행",
    "하나은행",
    "농협은행",
    "기업은행",
    "산업은행",
    "수출입은행",
    "금융감독원",
    "금융위원회",
    "한국은행",
    "국민연금",
    "예금보험공사",
    "신용보증기금",
    "기술보증기금",
)

_ENTITY_SUFFIXES = (
    "은행",
    "증권",
    "보험",
    "카드",
    "캐피탈",
    "저축은행",
    "자산운용",
    "거래소",
)


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
    text = re.sub(r"[‘’'\"“”·…,:;!?/\\|_+=~`<>{}]", " ", text)
    text = re.sub(r"[-–—]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def _title_similarity(a: str, b: str) -> float:
    na = _normalize_title(a)
    nb = _normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _meaningful_overlap(a_tokens: set[str], b_tokens: set[str]) -> bool:
    shared = (a_tokens & b_tokens) - _GENERIC_TOKENS - _STOPWORDS
    return len(shared) >= 2 or bool(shared & (_extract_entities(" ".join(a_tokens)) | _extract_entities(" ".join(b_tokens))))


def _should_cluster(a: TaggedArticle, b: TaggedArticle) -> bool:
    title_a = _article_title(a)
    title_b = _article_title(b)
    norm_a = _normalize_title(title_a)
    norm_b = _normalize_title(title_b)
    if not norm_a or not norm_b:
        return False
    if norm_a == norm_b:
        return True

    same_sector = _primary_sector(a) == _primary_sector(b)
    sim = _title_similarity(title_a, title_b)
    if not same_sector:
        return sim >= 0.90

    if min(len(norm_a), len(norm_b)) < 12:
        return False

    tokens_a = _tokenize_title(title_a)
    tokens_b = _tokenize_title(title_b)
    entities_a = _extract_entities(title_a)
    entities_b = _extract_entities(title_b)
    numbers_a = _extract_numbers(title_a)
    numbers_b = _extract_numbers(title_b)

    shared_entities = entities_a & entities_b
    shared_numbers = numbers_a & numbers_b
    token_jaccard = _jaccard(tokens_a - _GENERIC_TOKENS, tokens_b - _GENERIC_TOKENS)

    if sim >= 0.78 and _meaningful_overlap(tokens_a, tokens_b):
        return True
    if token_jaccard >= 0.55 and (shared_entities or shared_numbers):
        return True
    if shared_entities and shared_numbers:
        return True
    return False


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
    return str(
        _article_field(article, "naver_link")
        or _article_field(article, "originallink")
        or _article_field(article, "link")
        or ""
    ).strip()


def _representative_score(item: TaggedArticle) -> tuple[float, int, int, float, str, str]:
    title = _article_title(item).strip()
    desc = str(_article_field(item.article, "description") or "").strip()
    title_len_score = min(len(title), 90) - max(len(title) - 110, 0)
    metadata_score = (2 if desc else 0) + (1 if _link_value(item) else 0)
    pub_date = _article_field(item.article, "pub_date")
    timestamp = float(pub_date.timestamp()) if hasattr(pub_date, "timestamp") else 0.0
    return (
        _relevance_value(item),
        title_len_score,
        metadata_score,
        timestamp,
        _normalize_title(title),
        _link_value(item),
    )


def _related_metadata(item: TaggedArticle) -> dict[str, str]:
    article = item.article
    return {
        "title": _article_title(item),
        "link": _link_value(item),
        "press": str(_article_field(article, "press") or _article_field(article, "publisher") or ""),
        "pub_date": str(_article_field(article, "pub_date") or ""),
    }


def _cluster_id(members: list[TaggedArticle]) -> str:
    normalized_titles = sorted(_normalize_title(_article_title(member)) for member in members)
    seed = "|".join(normalized_titles).encode("utf-8")
    return "issue-" + hashlib.sha1(seed).hexdigest()[:12]


def cluster_tagged_articles(tagged: list[TaggedArticle]) -> list[TaggedArticle]:
    clusters: list[list[TaggedArticle]] = []
    for item in tagged:
        target: list[TaggedArticle] | None = None
        for cluster in clusters:
            if any(_should_cluster(item, member) for member in cluster):
                target = cluster
                break
        if target is None:
            clusters.append([item])
        else:
            target.append(item)

    representatives: list[TaggedArticle] = []
    for cluster in clusters:
        representative = max(cluster, key=_representative_score)
        cid = _cluster_id(cluster)
        size = len(cluster)
        non_representatives = [item for item in cluster if item is not representative]

        for rank, item in enumerate(sorted(cluster, key=_representative_score, reverse=True), start=1):
            setattr(item.article, "cluster_id", cid)
            setattr(item.article, "cluster_size", size)
            setattr(item.article, "cluster_rank", rank)
            setattr(item.article, "cluster_is_representative", item is representative)
            setattr(item.article, "related_count", max(size - 1, 0))
            setattr(item.article, "related_articles", [])

        related = [_related_metadata(item) for item in non_representatives[:5]]
        setattr(representative.article, "related_articles", related)
        representatives.append(representative)

    return representatives
