from __future__ import annotations

from datetime import datetime, timedelta

from src.pipeline.normalize import Article
from src.pipeline.report import _select_top_items, top_report_items, visible_report_items
from src.pipeline.tagger import TaggedArticle


def _mk(
    idx: int,
    title: str,
    *,
    sector: str = "은행",
    topics: list[str] | None = None,
    score: float = 8.0,
    cluster_id: str | None = None,
    publisher: str | None = None,
) -> TaggedArticle:
    article = Article(
        title=title,
        description=title,
        link=f"https://example.com/{idx}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 6, 22, 12, 0) - timedelta(minutes=idx),
        query="test",
        relevance_score=score,
        cluster_id=cluster_id,
        cluster_size=2 if cluster_id else 1,
    )
    if publisher is not None:
        setattr(article, "press", publisher)
    return TaggedArticle(
        article=article,
        sectors=[sector],
        topics=topics or [],
        matched_keywords=[],
    )


def _titles(items: list[TaggedArticle]) -> list[str]:
    return [item.article.title for item in items]


def test_regulatory_and_risk_rank_above_low_value_items_with_similar_scores():
    items = [
        _mk(1, "다음주 한국은행 및 금융위·금감원 일정", sector="입법·정책", topics=["일정·브리핑"]),
        _mk(2, "칼럼 금리 인하 시기와 금융시장", sector="거시·시장", topics=["칼럼·오피니언"]),
        _mk(3, "은행권 사회공헌 캠페인 확대", sector="은행", topics=["업계동정·사회공헌"]),
        _mk(4, "금감원, 저축은행 검사 착수", sector="감독·제재", topics=["감독·제재"]),
        _mk(5, "SNS 얼굴 박제 불법추심 피해 확산", sector="대부", topics=["불법사금융"]),
    ]
    titles = _titles(_select_top_items(items, limit=5))
    assert titles.index("금감원, 저축은행 검사 착수") < titles.index("다음주 한국은행 및 금융위·금감원 일정")
    assert titles.index("SNS 얼굴 박제 불법추심 피해 확산") < titles.index("은행권 사회공헌 캠페인 확대")


def test_low_value_items_remain_visible_but_deprioritized_in_top_items():
    items = [
        _mk(1, "다음주 한국은행 및 금융위·금감원 일정", sector="입법·정책", topics=["일정·브리핑"], score=8.3),
        _mk(2, "은행권 사회공헌 캠페인 확대", sector="은행", topics=["업계동정·사회공헌"], score=8.3),
        _mk(3, "금감원, 저축은행 검사 착수", sector="감독·제재", topics=["감독·제재"], score=8.1),
    ]
    assert set(_titles(visible_report_items(items))) == set(_titles(items))
    top_titles = _titles(top_report_items(items, limit=3))
    assert top_titles[0] == "금감원, 저축은행 검사 착수"
    assert set(top_titles) == set(_titles(items))


def test_top_selection_is_deterministic_with_content_type_adjustments():
    items = [
        _mk(1, "금융 브리핑 NH농협은행·신협중앙회·새마을금고"),
        _mk(2, "저축은행 예금금리 4%대 재진입", sector="저축은행"),
        _mk(3, "금융위 금융권 제도개선 방안 발표", sector="입법·정책", topics=["정책·제도개선"]),
    ]
    assert _titles(top_report_items(items, limit=3)) == _titles(top_report_items(list(reversed(items)), limit=3))


def test_unknown_regulatory_risk_ranks_above_stock_promo_with_similar_score():
    regulatory = _mk(
        1, "불법사금융 피해 확산 금감원 제재 착수", sector="감독·제재", topics=["불법사금융"], score=8.0
    )
    regulatory.article.link = "https://news.naver.com/main/read.naver?oid=1"
    promo = _mk(
        2, "특징주 은행주 급등 투자자 관심", sector="은행", topics=["증시·시장시황"], score=8.0, publisher="증권속보"
    )

    assert _titles(top_report_items([promo, regulatory], limit=2))[0] == regulatory.article.title


def test_major_finance_hard_news_ranks_above_unknown_low_information():
    hard_news = _mk(
        1, "저축은행 건전성 점검 강화", sector="저축은행", topics=["건전성"], score=8.0, publisher="매일경제"
    )
    low_info = _mk(2, "오늘의 금융 브리핑 모음", sector="저축은행", topics=["일정·브리핑"], score=8.0)
    low_info.article.link = "https://news.naver.com/main/read.naver?oid=2"

    assert _titles(top_report_items([low_info, hard_news], limit=2))[0] == hard_news.article.title

def test_cluster_title_dedupe_is_preserved():
    items = [
        _mk(1, "금감원 은행권 검사 착수", sector="감독·제재", cluster_id="same"),
        _mk(2, "금감원 은행권 검사 확대", sector="감독·제재", cluster_id="same"),
        _mk(3, "대부 불법추심 단속", sector="대부"),
    ]
    tops = top_report_items(items, limit=10)
    assert len(tops) == 2
    assert sum(1 for item in tops if item.article.cluster_id == "same") == 1


def test_only_low_value_items_still_fill_top_items():
    items = [
        _mk(1, "다음주 한국은행 및 금융위·금감원 일정", sector="입법·정책", topics=["일정·브리핑"]),
        _mk(2, "칼럼 금리 인하 시기와 금융시장", sector="거시·시장", topics=["칼럼·오피니언"]),
        _mk(3, "은행권 사회공헌 캠페인 확대", sector="은행", topics=["업계동정·사회공헌"]),
    ]
    assert len(top_report_items(items, limit=10)) == 3
