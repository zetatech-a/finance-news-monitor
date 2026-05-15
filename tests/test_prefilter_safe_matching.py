from datetime import datetime

from src.pipeline.filtering import filter_articles
from src.pipeline.normalize import Article


def _article(title: str, description: str = "") -> Article:
    return Article(
        title=title,
        description=description,
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 15, 9, 0),
        query="test",
    )


def test_prefilter_keeps_financial_supervision_and_macro_cycle_articles():
    articles = [
        _article("금융감독원, 은행권 대출 검사 착수"),
        _article("경기침체 우려에 은행 연체율 상승"),
        _article("경기 둔화에 한국은행 기준금리 인하 전망"),
    ]
    assert filter_articles(articles) == articles


def test_prefilter_drops_obvious_sports_and_entertainment():
    sports = _article("프로야구 감독 경질")
    soccer = _article("축구 경기 결과")
    entertainment = _article("배우 드라마 출연")
    assert filter_articles([sports, soccer, entertainment]) == []
