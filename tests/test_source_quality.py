from __future__ import annotations

from datetime import datetime

from src.pipeline.normalize import Article
from src.pipeline.source_quality import (
    classify_source_quality,
    normalize_publisher_name,
    publisher_name,
    source_quality_rank_adjustment,
)
from src.pipeline.tagger import TaggedArticle


def _article(
    title: str, *, description: str = "", publisher: str | None = None, field: str = "press"
) -> Article:
    article = Article(
        title=title,
        description=description or title,
        link="https://example.com/article",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 6, 24, 9, 0),
        query="test",
        relevance_score=8,
    )
    if publisher is not None:
        setattr(article, field, publisher)
    return article


def _tagged(
    article: Article, *, sectors: list[str] | None = None, topics: list[str] | None = None
) -> TaggedArticle:
    return TaggedArticle(
        article=article, sectors=sectors or ["은행"], topics=topics or [], matched_keywords=[]
    )


def test_missing_publisher_classifies_unknown_with_small_penalty():
    item = _tagged(_article("은행권 대출금리 동향 점검"))

    assert publisher_name(item) == "example.com"
    item.article.link = "https://news.naver.com/main/read.naver?oid=1"
    assert classify_source_quality(item) == "unknown"
    assert source_quality_rank_adjustment(item) == -0.3


def test_regulator_like_source_classifies_as_regulatory():
    item = _tagged(_article("가계대출 관리방안 발표", publisher="금융위원회"), sectors=["입법·정책"])

    assert classify_source_quality(item) == "regulatory"
    assert source_quality_rank_adjustment(item) == 0.8


def test_primary_requires_clear_official_signal():
    ordinary_media = _tagged(_article("은행 신상품 출시", publisher="OO은행 뉴스"))
    ordinary_disclosure = _tagged(_article("은행 자본확충 공시", publisher="OO경제"))
    official = _tagged(_article("은행 공식 보도자료: 금융상품 공시", publisher="OO은행 공식 홈페이지"))

    assert classify_source_quality(ordinary_media) == "unknown"
    assert classify_source_quality(ordinary_disclosure) == "major_finance"
    assert classify_source_quality(official) == "primary"


def test_finance_or_specialist_media_classification():
    finance = _tagged(_article("저축은행 건전성 점검", publisher="매일경제"))
    specialist = _tagged(_article("보험사 지급여력 동향", publisher="보험매일"))

    assert classify_source_quality(finance) == "major_finance"
    assert classify_source_quality(specialist) in {"major_finance", "specialist"}


def test_stock_promo_title_classifies_as_promo_snippet():
    item = _tagged(_article("특징주 은행주 급등 투자자 관심", publisher="증권속보"))

    assert classify_source_quality(item) == "promo_or_stock_snippet"
    assert source_quality_rank_adjustment(item) == -1.2


def test_press_release_event_title_classifies_as_low_value():
    campaign = _tagged(_article("은행권 사회공헌 캠페인 확대", publisher="지역뉴스"))
    mou = _tagged(_article("저축은행 업무협약 체결", publisher="지역뉴스"))

    assert classify_source_quality(campaign) == "press_release_like"
    assert classify_source_quality(mou) == "press_release_like"


def test_strong_regulatory_risk_missing_publisher_is_not_overly_penalized():
    regulatory_article = _article("불법사금융 피해 확산 금감원 제재 착수")
    regulatory_article.link = "https://news.naver.com/main/read.naver?oid=1"
    regulatory_item = _tagged(regulatory_article, sectors=["감독·제재"], topics=["불법사금융"])
    unknown_risk_article = _article("저축은행 연체율 부실채권 급증")
    unknown_risk_article.link = "https://news.naver.com/main/read.naver?oid=2"
    unknown_risk_item = _tagged(unknown_risk_article, sectors=["저축은행"], topics=["건전성"])

    assert classify_source_quality(regulatory_item) == "regulatory"
    assert source_quality_rank_adjustment(regulatory_item) == 0.8
    assert classify_source_quality(unknown_risk_item) == "unknown"
    assert source_quality_rank_adjustment(unknown_risk_item) == -0.1


def test_normalize_publisher_name_removes_safe_suffix_noise():
    assert normalize_publisher_name(" [매일경제 | 네이버뉴스] ") == "매일경제"
    assert normalize_publisher_name("대한금융신문 언론사") == "대한금융신문"
