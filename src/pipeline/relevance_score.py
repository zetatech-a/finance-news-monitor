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
        "불법사채": 6,
        "불법대부": 6,
        "불법추심": 6,
        "대출광고": 4,
        "대부광고": 4,
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
        "보이스피싱": 6,
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
        "가상자산": 4,
        "암호화폐": 4,
        "토큰증권": 5,
        "sto": 5,
        "디지털자산": 4,
        "가상자산거래소": 5,
        "디지털자산거래소": 5,
        "코인거래소": 4,
        "업비트": 5,
        "빗썸": 5,
        "두나무": 5,
        "코빗": 4,
        "고팍스": 4,
        "fiu": 5,
        "금융정보분석원": 5,
        "스테이블코인": 5,
        "원화 스테이블코인": 6,
        # securities liquidity/funding/policy
        "유동성비율": 5,
        "신조정유동성비율": 5,
        "조정유동성비율": 4,
        "신 ncr": 4,
        "ncr": 3,
        "순자본비율": 4,
        "금융투자업규정": 5,
        "레고랜드 사태": 4,
        "cp시장": 4,
        "여전채": 5,
        "카드채": 5,
        "캐피탈채": 5,
        "정책금융": 5,
        "산업은행": 5,
        "산은": 4,
        "기업은행": 5,
        "기은": 4,
        "국민성장펀드": 5,
        "성장펀드": 4,
        "생산적 금융": 4,
        "첨단전략산업기금": 5,
        "정책금융기관": 5,
        "신용보증기금": 5,
        "기술보증기금": 5,
        # overseas with Korea market linkage anchors
        "미 국채금리": 4,
        "미국채 금리": 4,
        "글로벌 채권금리": 4,
        "원달러": 4,
        "원/달러": 4,
        "외환시장": 4,
        "국내 채권시장": 4,
        "은행권 대출금리": 4,
        "국내 금융시장": 4,
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

# NOTE: 아래 세 목록은 relevance_filter.py에서도 import해 사용하는 단일 출처(canonical)다.
# 여기 말고 다른 곳에 복사본을 만들지 말 것 (과거 복사본 간 drift가 실제로 발생했음).
STRONG_FINANCE_ANCHORS: tuple[str, ...] = (
    "불법사금융", "불법사채", "불법대부", "미등록대부", "대부업", "대부업체",
    "채권추심", "불법추심", "보이스피싱", "스미싱", "대포통장",
    "불법 대출광고", "대출광고", "대부광고", "금융위", "금융위원회", "금감원", "금융감독원",
    "금융당국", "저축은행", "은행권", "시중은행", "인터넷은행", "카드론",
    "현금서비스", "여전채", "보험사", "증권사", "상호금융", "신협", "새마을금고",
    "연체율", "부실채권", "가계대출", "주담대", "기업대출", "부동산 pf", "부동산pf",
    "pf", "익스포저", "워크아웃",
)

FINANCE_RISK_OR_REGULATORY_SIGNALS: tuple[str, ...] = (
    "피해", "협박", "단속", "점검", "검사", "착수", "경고", "경고등", "상승",
    "연체", "연체율", "부실", "부실채권", "불법", "미등록", "추심", "광고",
    "대출광고", "금리", "예금금리", "재진입", "당국", "제재", "과징금", "행정처분",
    "악용", "조직", "확산", "리스크", "위험", "관리", "워크아웃", "익스포저",
)

CAPPED_NOISE_TERMS: tuple[str, ...] = (
    "sns",
    "유튜브",
    "맛집",
    "인플루언서",
    "행사",
    "이벤트",
    "루머",
    "먹방",
    "여행",
    "축제",
)

_STRONG_CONTEXT_NEGATIVE_CAP = 1


def _has_strong_finance_anchor(text: str) -> bool:
    return has_any_term(text, STRONG_FINANCE_ANCHORS)


def _has_finance_risk_or_regulatory_signal(text: str) -> bool:
    return has_any_term(text, FINANCE_RISK_OR_REGULATORY_SIGNALS)


def _cap_negative_for_strong_finance_context(text: str, neg_score: int) -> int:
    matched_negative = find_terms(text, _WEIGHTS.neg)
    if (
        neg_score > _STRONG_CONTEXT_NEGATIVE_CAP
        and matched_negative
        and set(matched_negative).issubset(set(CAPPED_NOISE_TERMS))
        and _has_strong_finance_anchor(text)
        and _has_finance_risk_or_regulatory_signal(text)
    ):
        return _STRONG_CONTEXT_NEGATIVE_CAP
    return neg_score


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
    neg_score = _cap_negative_for_strong_finance_context(text, neg_score)

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
