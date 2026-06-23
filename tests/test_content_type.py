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
