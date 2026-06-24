from __future__ import annotations

from datetime import datetime

from src.pipeline.content_type import classify_content_type
from src.pipeline.normalize import Article
from src.pipeline.tagger import TaggedArticle


def _item(title: str, *, sector: str = "은행", topics: list[str] | None = None, description: str | None = None) -> TaggedArticle:
    return TaggedArticle(
        article=Article(
            title=title,
            description=description or title,
            link="https://example.com",
            originallink=None,
            naver_link=None,
            pub_date=datetime(2026, 6, 22, 9, 0),
            query="test",
            relevance_score=8,
        ),
        sectors=[sector],
        topics=topics or [],
        matched_keywords=[],
    )


def test_classifies_schedule_notice():
    assert classify_content_type(_item("다음주 한국은행 및 금융위·금감원 일정")) == "schedule"


def test_classifies_opinion_column():
    assert classify_content_type(_item("칼럼 금리 인하 시기와 금융시장")) == "opinion"


def test_classifies_social_contribution_before_generic_event():
    assert classify_content_type(_item("은행권 사회공헌 캠페인 확대")) == "local_social"


def test_classifies_finance_briefing():
    assert classify_content_type(_item("금융 브리핑 NH농협은행·신협중앙회·새마을금고")) == "briefing"


def test_classifies_regulatory_action():
    assert classify_content_type(_item("금감원, 저축은행 검사 착수", sector="감독·제재")) == "regulatory"


def test_classifies_illegal_collection_risk():
    assert classify_content_type(_item("SNS 얼굴 박제 불법추심 피해 확산", sector="대부")) == "risk"


def test_classifies_deposit_rate_product():
    assert classify_content_type(_item("저축은행 예금금리 4%대 재진입", sector="저축은행")) == "product"


def test_classifies_simple_market_close_as_price_quote():
    assert classify_content_type(_item("뉴욕증시, 연준 경계감에 혼조 마감", sector="거시·시장", topics=["해외·글로벌"])) == "price_quote"


def test_schedule_word_does_not_override_illegal_lending_enforcement():
    assert classify_content_type(_item("다음주 불법 대출광고 특별단속", sector="대부")) in {"regulatory", "risk"}


def test_schedule_word_does_not_override_regulatory_field_inspection():
    assert classify_content_type(_item("이번주 금감원 저축은행 현장점검 착수", sector="감독·제재")) == "regulatory"


def test_week_marker_does_not_force_schedule_for_regulatory_title():
    assert classify_content_type(_item("이번주 금감원 저축은행 검사 착수")) == "regulatory"


def test_next_week_marker_does_not_force_schedule_for_regulatory_title():
    assert classify_content_type(_item("다음주 금융위 불완전판매 제재 착수")) == "regulatory"


def test_week_marker_does_not_force_schedule_for_risk_title():
    assert classify_content_type(_item("이번주 저축은행 연체율 급등")) == "risk"


def test_explicit_week_schedule_title_still_classifies_as_schedule():
    assert classify_content_type(_item("이번 주 금융권 일정")) == "schedule"
    assert classify_content_type(_item("다음 주 금융위 회의 일정")) == "schedule"


def test_profile_term_does_not_match_inside_personal_business_owner():
    assert classify_content_type(_item("개인사업자 대출금리 인하")) in {"product", "hard_news"}
    assert classify_content_type(_item("개인사업자 금융지원 확대")) != "profile"


def test_profile_terms_still_classify_personnel_articles():
    assert classify_content_type(_item("금융지주 임원 인사 발표")) == "profile"
    assert classify_content_type(_item("은행장 선임")) == "profile"
    assert classify_content_type(_item("대표이사 취임")) == "profile"


def test_financial_crisis_does_not_match_regulator_shorthand():
    assert classify_content_type(_item("금융위기 우려에 시장 변동성 확대")) != "regulatory"
    assert classify_content_type(_item("금융위기 이후 은행권 건전성 점검")) != "regulatory"


def test_regulator_shorthand_still_classifies_regulatory_action():
    assert classify_content_type(_item("금융위, 저축은행 검사 착수", sector="감독·제재")) == "regulatory"
    assert classify_content_type(_item("금융위원회 저축은행 현장점검 확대")) == "regulatory"


def test_body_only_briefing_does_not_override_material_policy_article():
    result = classify_content_type(
        _item(
            "금융위 청년 적금 지원방안 발표",
            sector="입법·정책",
            description="금융위 관계자는 브리핑에서 세부 내용을 설명했다",
        )
    )
    assert result != "briefing"


def test_briefing_title_formats_still_classify_as_briefing():
    assert classify_content_type(_item("오늘의 은행 주요 소식")) == "briefing"


def test_metadata_regulatory_sector_with_article_action_only_does_not_create_regulatory_classification():
    result = classify_content_type(_item("저축은행 검사 착수", sector="감독·제재"))
    assert result != "regulatory"


def test_schedule_title_variants_still_classify_as_schedule():
    schedule_titles = [
        "오늘의 일정",
        "오늘 일정",
        "주요일정",
        "주요 일정",
        "금융권 일정",
        "증시 일정",
        "시장 일정",
        "이번 주 일정",
        "주간 일정",
        "월간 일정",
        "경제 캘린더",
        "금융위 일정",
        "금감원 일정",
        "거래소 일정",
        "한은 일정",
        "금융위원회 주간 일정",
        "금융감독원 주간 일정",
    ]

    for title in schedule_titles:
        assert classify_content_type(_item(title, description="회의와 설명회 일정 포함")) == "schedule"


def test_metadata_only_regulatory_label_does_not_create_regulatory_classification():
    result = classify_content_type(
        {
            "article": {
                "title": "은행권 사회공헌 활동 확대",
                "summary": "지역 취약계층 지원 프로그램을 발표했다.",
            },
            "topic": "규제",
            "category": "업무·법적 / 규제",
            "sector": "금융감독",
            "labels": ["regulatory"],
        }
    )
    assert result not in {"regulatory", "risk"}


def test_explicit_schedule_title_survives_generic_body_risk_terms():
    assert (
        classify_content_type(
            {
                "article": {
                    "title": "금융위원회 주간 일정",
                    "summary": "금융감독, 증권시장, 소비자보호 관련 회의 일정 포함",
                    "body": "금융감독, 증권시장, 소비자보호 관련 회의 일정 포함",
                }
            }
        )
        == "schedule"
    )


def test_bare_loan_ad_term_is_not_strong_risk_signal():
    result = classify_content_type(
        _item(
            "은행, 주택담보대출 광고 캠페인 공개",
            description="신규 대출 상품 홍보를 시작했다.",
        )
    )
    assert result not in {"regulatory", "risk"}


def test_illegal_loan_ad_context_remains_risk_signal():
    assert (
        classify_content_type(
            _item(
                "불법 대출 광고 단속 강화",
                sector="대부",
                description="무등록 대부업체와 사채 광고 피해가 증가했다.",
            )
        )
        in {"regulatory", "risk"}
    )


def test_strong_regulatory_loan_ad_article_still_classifies():
    assert (
        classify_content_type(
            _item(
                "금감원, 불법 대출 광고 단속 강화",
                sector="대부",
                description="무등록 대부업체에 대한 제재를 예고했다.",
            )
        )
        in {"regulatory", "risk"}
    )
