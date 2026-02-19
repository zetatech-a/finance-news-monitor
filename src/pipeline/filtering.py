from __future__ import annotations

from typing import Iterable

from src.pipeline.normalize import Article


SPORTS_KEYWORDS = (
    "프로배구",
    "프로야구",
    "득점",
    "승부",
    "경기",
    "선수",
    "감독",
)

POLITICS_KEYWORDS = (
    "총선",
    "자민당",
    "개헌",
    "내각",
    "국회",
    "대통령",
    "장관",
)

# 정치 기사와의 구분/예외처리(정치+금융이면 keep)
FINANCE_KEYWORDS = (
    "금리",
    "대출",
    "연체",
    "부실",
    "금감원",
    "금융위",
    "은행",
    "증권",
    "보험",
    "카드",
    "회사채",
    "pf",
    "가계부채",
)

# 엔터/방송/연예성 기사 차단용(수집 단계에서 노이즈를 최대한 줄인다)
ENTERTAINMENT_DOMAINS = (
    "entertain.naver.com",
    "m.entertain.naver.com",
    "sports.naver.com",
    "m.sports.naver.com",
    "star.mt.co.kr",
    "sportschosun.com",
    "sports.donga.com",
    "sportsseoul.com",
    "osen.co.kr",
    "xportsnews.com",
    "dispatch.co.kr",
    "newsen.com",
    "stoo.com",
    "isplus.com",
)

ENTERTAINMENT_KEYWORDS = (
    # 방송/연예
    "예능",
    "연예",
    "연예계",
    "배우",
    "가수",
    "아이돌",
    "드라마",
    "영화",
    "시청률",
    "방송",
    "tv",
    "ost",
    "화보",
    "팬",
    "콘서트",
    "뮤지컬",
    "유튜브",
    "인스타",
    "sns",
    "틱톡",
    # 가십/사생활
    "열애",
    "공개열애",
    "결혼",
    "이혼",
    "임신",
    "출산",
    "근황",
    "해명",
    "캡처",
    # 특정 프로그램 태그(예: '(이숙캠)')
    "이숙캠",
)

# '대부'가 임대(공유재산) 맥락으로 쓰이는 비금융 노이즈를 차단
PUBLIC_LEASE_KEYWORDS = (
    "공유재산",
    "대부계약",
    "대부료",
    "사용허가",
    "점용",
    "임대차",
    "임대료",
    "사용료",
    "허가",
)

# 엔터/잡음 키워드가 있어도 살릴 만한 '강한' 금융 앵커
FINANCE_STRONG_ANCHORS = (
    "금융감독원",
    "금감원",
    "금융위원회",
    "금융위",
    "한국은행",
    "한은",
    "연준",
    "fed",
    "ecb",
    "boj",
    "은행",
    "저축은행",
    "카드사",
    "캐피탈",
    "증권사",
    "보험사",
    "대부업",
    "대부업법",
    "불법사금융",
    "채권추심",
    "npl",
    "부실채권",
    "회사채",
    "abcp",
    "ipo",
    "pf",
    "프로젝트파이낸싱",
    "코스피",
    "코스닥",
    "환율",
    "국채",
    "가상자산",
    "토큰증권",
)


def _get_text_fields(article: Article | dict) -> str:
    if isinstance(article, Article):
        values = (
            article.title,
            article.description,
            article.query,
            article.link,
            article.originallink or "",
        )
    else:
        values = (
            article.get("title", ""),
            article.get("description", ""),
            article.get("summary", ""),
            article.get("content", ""),
            article.get("query", ""),
            article.get("link", ""),
            article.get("url", ""),
            article.get("originallink", ""),
        )
    return " ".join(str(value) for value in values if value).lower()


def _get_urls(article: Article | dict) -> tuple[str, ...]:
    if isinstance(article, Article):
        urls = (article.link, article.originallink or "", article.naver_link or "")
    else:
        urls = (
            article.get("link", ""),
            article.get("url", ""),
            article.get("originallink", ""),
            article.get("naver_link", ""),
        )
    return tuple(str(url) for url in urls if url)


def filter_articles(articles: Iterable[Article]) -> list[Article]:
    """Lightweight rule-based pre-filter.

    Goal: drop obvious non-finance noise early (sports/entertainment/pure politics),
    so later relevance scoring/model can focus on real candidates.
    """

    filtered: list[Article] = []
    for article in articles:
        urls = _get_urls(article)
        if any(dom in url for dom in ENTERTAINMENT_DOMAINS for url in urls):
            continue

        # sports naver explicit
        if any("sports.naver.com" in url for url in urls):
            continue

        text = _get_text_fields(article)

        # sports keyword
        if any(keyword in text for keyword in SPORTS_KEYWORDS):
            continue

        # politics-only
        has_politics = any(keyword in text for keyword in POLITICS_KEYWORDS)
        has_finance = any(keyword in text for keyword in FINANCE_KEYWORDS)
        if has_politics and not has_finance:
            continue

        # public lease '대부' noise (공유재산 대부계약 등)
        has_public_lease = any(k in text for k in PUBLIC_LEASE_KEYWORDS)
        has_finance_strong = any(k in text for k in FINANCE_STRONG_ANCHORS)
        if has_public_lease and not has_finance_strong:
            continue

        # entertainment hints: drop unless strong finance anchors exist
        if any(k in text for k in ENTERTAINMENT_KEYWORDS) and not has_finance_strong:
            continue

        filtered.append(article)

    return filtered
