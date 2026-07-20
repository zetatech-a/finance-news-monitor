from __future__ import annotations

from typing import Iterable

from src.pipeline.normalize import Article
from src.pipeline.text_matcher import find_terms, has_any_term, normalize_text


SPORTS_KEYWORDS = (
    "프로배구",
    "프로야구",
    "프로축구",
    "축구 경기",
    "경기 결과",
    "감독 경질",
    "득점",
    "승부",
    "선수",
    "구단",
    "k리그",
    "mlb",
    "epl",
    "월드컵",
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

# 명백한 연예/방송 신호 — 단독으로도 드랍 (강한 금융 앵커가 있으면 구제)
ENTERTAINMENT_STRONG_KEYWORDS = (
    "예능",
    "연예",
    "연예계",
    "배우",
    "가수",
    "아이돌",
    "드라마",
    "시청률",
    "ost",
    "화보",
    "콘서트",
    "뮤지컬",
    "열애",
    "공개열애",
    "이혼",
    # 특정 프로그램 태그(예: '(이숙캠)')
    "이숙캠",
)

# 일상어와 겹치는 약한 신호 — 단독으로는 드랍하지 않고 2개 이상 겹칠 때만 드랍.
# 예: "배우 ○○ 결혼"(배우=강)은 드랍되지만 "결혼 비용 대출 수요"(결혼 1개)는 통과.
ENTERTAINMENT_WEAK_KEYWORDS = (
    "영화",
    "방송",
    "tv",
    "팬",
    "유튜브",
    "인스타",
    "sns",
    "틱톡",
    "결혼",
    "임신",
    "출산",
    "근황",
    "해명",
    "캡처",
)

# 하위호환: 기존 이름을 참조하는 코드/테스트용 (드랍 판정은 강/약 분리 로직 사용)
ENTERTAINMENT_KEYWORDS = ENTERTAINMENT_STRONG_KEYWORDS + ENTERTAINMENT_WEAK_KEYWORDS

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
    "불법 사금융",  # 띄어 쓴 표기 — 실데이터에서 이 표기가 구제에 안 잡혀 유실된 사례 있음
    "사금융",
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
    # URL은 키워드 매칭 대상에서 제외한다 — 경로/도메인 문자열('.tv' 등)이
    # 엔터/스포츠 키워드로 오탐되는 것을 막는다. 도메인 차단은 _get_urls()로
    # ENTERTAINMENT_DOMAINS에서 정밀하게 처리한다.
    if isinstance(article, Article):
        values = (
            article.title,
            article.description,
            article.query,
        )
    else:
        values = (
            article.get("title", ""),
            article.get("description", ""),
            article.get("summary", ""),
            article.get("content", ""),
            article.get("query", ""),
        )
    return normalize_text(" ".join(str(value) for value in values if value))


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


def is_blocked_source_url(url: str) -> bool:
    """엔터/스포츠 차단 도메인 URL 여부.

    dedup 흡수분(duplicate_sources)처럼 1차/2차 필터를 거치지 않고 리포트에
    노출되는 메타데이터를 내보내기 전에 최소한의 도메인 검사를 하기 위한 용도.
    """
    value = (url or "").strip()
    if not value:
        return False
    return any(domain in value for domain in ENTERTAINMENT_DOMAINS)


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
        if has_any_term(text, SPORTS_KEYWORDS):
            continue

        # politics-only
        has_politics = has_any_term(text, POLITICS_KEYWORDS)
        has_finance = has_any_term(text, FINANCE_KEYWORDS)
        if has_politics and not has_finance:
            continue

        # public lease '대부' noise (공유재산 대부계약 등)
        has_public_lease = has_any_term(text, PUBLIC_LEASE_KEYWORDS)
        has_finance_strong = has_any_term(text, FINANCE_STRONG_ANCHORS)
        if has_public_lease and not has_finance_strong:
            continue

        # entertainment hints: 강한 신호 1개 또는 약한 신호 2개 이상이면 드랍
        # (강한 금융 앵커가 있으면 구제). 약한 신호 1개만으로는 드랍하지 않는다.
        if not has_finance_strong:
            if has_any_term(text, ENTERTAINMENT_STRONG_KEYWORDS):
                continue
            if len(find_terms(text, ENTERTAINMENT_WEAK_KEYWORDS)) >= 2:
                continue

        filtered.append(article)

    return filtered
