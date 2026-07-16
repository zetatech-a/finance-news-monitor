from __future__ import annotations

from src.pipeline.filtering import filter_articles
from src.pipeline.relevance_filter import (
    has_domain_anchor,
    has_regulator_policy_enforcement_context,
)
from src.pipeline.relevance_score import matched_terms, relevance_score


# ---------------------------------------------------------------------------
# ④ 집행 일반어(검사/제재/과징금/행정처분) — 금융 주체 문맥 가드
# ---------------------------------------------------------------------------

def test_prosecutor_article_is_not_domain_anchored():
    # '검사'가 검찰 검사(사람)를 뜻하는 비금융 기사
    text = "검찰, 전 대표 구속기소…담당 검사가 직접 심문에 나섰다"
    assert not has_domain_anchor(text)
    assert not has_regulator_policy_enforcement_context(text)


def test_non_finance_ftc_fine_is_not_domain_anchored():
    # 비금융 공정위 과징금 기사
    text = "공정위, 식품업체 가격 담합에 과징금 500억 부과"
    assert not has_domain_anchor(text)


def test_ftc_fine_on_card_companies_is_domain_anchored():
    # 금융 주체(카드사)가 함께 언급되면 공정위 과징금 기사도 앵커 인정
    text = "공정위, 카드사 가맹점 수수료 담합에 과징금 부과"
    assert has_domain_anchor(text)
    assert has_regulator_policy_enforcement_context(text)


def test_fss_inspection_still_domain_anchored():
    # 금감원은 자체가 무조건 앵커 — 기존 동작 유지
    text = "금감원, 검사 착수"
    assert has_domain_anchor(text)
    assert has_regulator_policy_enforcement_context(text)


def test_score_does_not_count_enforcement_terms_without_finance_entity():
    prosecutor = {"title": "검찰 검사, 제재 절차 착수", "summary": "행정처분과 과징금 부과 검토"}
    finance = {"title": "금감원, 저축은행 제재 절차 착수", "summary": "행정처분과 과징금 부과 검토"}

    # 비금융 기사는 집행 일반어가 hard로 집계되지 않아 keep 임계값(4) 미달이어야 함
    assert relevance_score(prosecutor) < 4
    assert relevance_score(finance) >= 4

    assert "제재" not in matched_terms(prosecutor)["hard"]
    assert "제재" in matched_terms(finance)["hard"]


# ---------------------------------------------------------------------------
# ⑥ 엔터테인먼트 2신호 규칙
# ---------------------------------------------------------------------------

def _stage1_keeps(title: str, description: str = "") -> bool:
    article = {"title": title, "description": description, "link": "https://example.com/a"}
    return len(filter_articles([article])) == 1


def test_single_weak_entertainment_signal_no_longer_drops():
    # '결혼' 하나만으로는 드랍하지 않는다 (금융 수요 기사일 수 있음)
    assert _stage1_keeps("결혼 비용 부담에 신용대출 수요 급증")


def test_two_weak_entertainment_signals_still_drop():
    assert not _stage1_keeps("유튜브 근황 공개로 화제가 된 인물")


def test_strong_entertainment_signal_still_drops_alone():
    assert not _stage1_keeps("배우 ○○, 깜짝 결혼 발표")


def test_strong_finance_anchor_still_rescues_entertainment_text():
    assert _stage1_keeps("저축은행 광고 모델 된 배우 ○○…금리 경쟁 심화")


def test_url_string_no_longer_triggers_keyword_match():
    # URL 경로의 'tv' 등이 엔터 키워드로 오탐되지 않아야 함
    assert _stage1_keeps(
        "가계대출 금리 동향", description="은행권 대출 금리",
    ) and len(filter_articles([{
        "title": "가계대출 금리 동향",
        "description": "은행권 대출 금리 분석",
        "link": "https://news.tv.example.com/tv/영화-특집/12345",
    }])) == 1
