from __future__ import annotations

from dataclasses import dataclass

from src.pipeline.text_matcher import contains_term, find_terms, has_any_term, normalize_text


# NOTE:
# - This is a lightweight heuristic scoring function used when ML model is absent
#   (or as a secondary signal). It is intentionally conservative: a single generic
#   token like "금리" should not be enough to pass.
# - The score is used by src/pipeline/relevance_filter.py.


@dataclass(frozen=True)
class _Weights:
    hard: dict[str, int]
    soft: dict[str, int]
    neg: dict[str, int]


_WEIGHTS = _Weights(
    # Strong "finance anchors" (institutions/products/regulatory terms)
    hard={
        "금융감독원": 6,
        "금감원": 6,
        "금융위원회": 6,
        "금융위": 6,
        "한국은행": 5,
        "한은": 5,
        "연준": 5,
        "fed": 5,
        "ecb": 4,
        "boj": 4,
        # institutions / sectors
        "은행": 3,
        "은행권": 4,
        "시중은행": 4,
        "인터넷은행": 4,
        "저축은행": 4,
        "카드사": 3,
        "캐피탈": 3,
        "증권사": 3,
        "보험사": 3,
        "보험업계": 3,
        "손해보험": 3,
        "생명보험": 3,
        "손보사": 3,
        "생보사": 3,
        "킥스": 5,
        "상호금융": 4,
        "새마을금고": 4,
        "신협": 4,
        # lending / debt collection
        "대부업": 5,
        "대부업법": 6,
        "최고금리": 4,
        "미등록대부": 6,
        "불법사금융": 8,
        "채권추심": 5,
        "npl": 4,
        "부실채권": 4,
        "연체율": 4,
        "충당금": 4,
        # markets
        "pf": 4,
        "부동산pf": 5,
        "프로젝트파이낸싱": 5,
        "회사채": 4,
        "abcp": 4,
        "cp": 3,
        "스프레드": 3,
        "유동성": 3,
        "ipo": 4,
        "공매도": 3,
        "불공정거래": 3,
        "카드론": 5,
        "여신금융협회": 6,
        "여신협회": 6,
        "여신전문금융협회": 6,
        "여신전문금융업": 6,
        "카드수수료": 3,
        "검사": 3,
        "제재": 3,
        "과징금": 3,
        "행정처분": 3,
        "제도개선": 3,
        "불완전판매": 4,
        "보이스피싱": 4,
        # macro
        "환율": 3,
        "국채": 3,
        "국채금리": 4,
        "기준금리": 3,
        "예대금리차": 4,
        "dsr": 4,
        "ltv": 3,
        "가계부채": 3,
        # digital assets
        "가상자산": 3,
        "암호화폐": 3,
        "토큰증권": 3,
        "sto": 3,
    },
    # Softer finance signals (generic words)
    soft={
        "금리": 1,
        "대출": 1,
        "대환": 2,
        "연체": 2,
        "부실": 2,
        "수수료": 1,
        "가맹점": 1,
        "보험료": 1,
        "실손": 1,
        "건전성": 2,
        "실적": 1,
        "순이익": 1,
        "주가": 1,
        "주식": 1,
        "코스피": 2,
        "코스닥": 2,
        "채권": 2,
        "달러": 1,
        "원화": 1,
        "금융": 1,
    },
    # Non-finance / entertainment / sports signals (negative)
    neg={
        # sports
        "프로야구": 6,
        "프로배구": 6,
        "선수": 4,
        "감독": 4,
        "경기": 4,
        "득점": 4,
        "우승": 4,
        # entertainment / gossip
        "예능": 8,
        "연예": 8,
        "배우": 4,
        "가수": 4,
        "아이돌": 4,
        "드라마": 6,
        "영화": 6,
        "시청률": 6,
        "방송": 6,
        "화보": 5,
        "유튜브": 4,
        "인스타": 4,
        "sns": 3,
        "열애": 6,
        "결혼": 4,
        "이혼": 6,
        "임신": 4,
        "출산": 4,
        "근황": 4,
        "캡처": 4,
        "이숙캠": 10,
        # other common noise
        "맛집": 4,
        "여행": 4,
        "날씨": 4,
        "운세": 4,
    },
)


def _text(article) -> str:
    """Extract text fields from dict/object."""
    if isinstance(article, dict):
        title = (article.get("title") or "").strip()
        summary = (article.get("summary") or article.get("description") or "").strip()
    else:
        title = (getattr(article, "title", "") or "").strip()
        summary = (
            (getattr(article, "summary", "") or "")
            or (getattr(article, "description", "") or "")
        ).strip()
    return f"{title}\n{summary}".strip()


def _urls(article) -> list[str]:
    if isinstance(article, dict):
        vals = [
            article.get("url", ""),
            article.get("link", ""),
            article.get("originallink", ""),
            article.get("naver_link", ""),
        ]
    else:
        vals = [
            getattr(article, "link", ""),
            getattr(article, "originallink", "") or "",
            getattr(article, "naver_link", "") or "",
        ]
    return [str(v).strip() for v in vals if v]


def relevance_score(article) -> int:
    """Return a conservative finance relevance score.

    Guidelines:
    - Needs at least one "hard" finance anchor to exceed the keep threshold.
    - Generic words (e.g., '금리') alone should not pass.
    """

    t = _text(article)
    if not t:
        return 0

    text = normalize_text(t)

    # Hard-block obvious entertainment/sports domains (defense in depth)
    urls = " ".join(_urls(article)).lower()
    if any(dom in urls for dom in ("entertain.naver.com", "sports.naver.com", "osen.co.kr", "sportschosun.com")):
        return -50

    hard_score = 0
    soft_score = 0
    neg_score = 0

    # add hard anchors
    for k, w in _WEIGHTS.hard.items():
        if contains_term(text, k):
            hard_score += w

    # add soft signals
    for k, w in _WEIGHTS.soft.items():
        if contains_term(text, k):
            soft_score += w

    # special-case: '사채' is too ambiguous; count only with proper context
    if contains_term(text, "사채") and has_any_term(text, ("불법", "대부업", "최고금리", "추심", "미등록", "금감원", "금융위")):
        soft_score += 2

    # special-case: '대부' used as lease context (공유재산 대부계약, 지명 등)
    # If '대부' appears but not '대부업', treat it as negative unless strong anchors exist.
    if contains_term(text, "대부") and not contains_term(text, "대부업") and has_any_term(text, ("공유재산", "대부계약", "대부료", "대부리", "대부도", "태권도")):
        neg_score += 10

    # negatives
    for k, w in _WEIGHTS.neg.items():
        if contains_term(text, k):
            neg_score += w

    score = hard_score + soft_score - neg_score

    # Conservative gating: without any hard finance anchor, cap score.
    # This prevents false positives like "고금리 사채" in entertainment articles.
    if hard_score <= 0:
        score = min(score, 2)

    return int(score)


def matched_terms(article) -> dict[str, list[str]]:
    """Return safe matched relevance terms for observability/debugging."""
    text = normalize_text(_text(article))
    return {
        "hard": find_terms(text, _WEIGHTS.hard),
        "soft": find_terms(text, _WEIGHTS.soft),
        "negative": find_terms(text, _WEIGHTS.neg),
    }
