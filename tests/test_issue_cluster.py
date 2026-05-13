from __future__ import annotations

from datetime import datetime, timedelta

from src.pipeline.issue_cluster import cluster_tagged_articles
from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import TaggedArticle


def _article(title: str, *, minutes: int = 0, score: int = 5) -> Article:
    article = Article(
        title=title,
        description="요약",
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=f"https://n.news.naver.com/{minutes}",
        pub_date=datetime(2026, 5, 11, 9, 0) + timedelta(minutes=minutes),
        query="test",
    )
    article.relevance_score = score
    return article


def _tagged(title: str, sector: str = "은행", *, minutes: int = 0, score: int = 5) -> TaggedArticle:
    return TaggedArticle(_article(title, minutes=minutes, score=score), [sector], [], [])


def test_similar_same_issue_titles_cluster_into_one_representative():
    tagged = [
        _tagged("카카오뱅크 1분기 순이익 1873억"),
        _tagged("카뱅, 1분기 순익 역대 최대", minutes=1),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 1
    assert representatives[0].article.cluster_size == 2


def test_unrelated_same_sector_articles_are_not_clustered():
    tagged = [
        _tagged("카카오뱅크 1분기 순이익 증가"),
        _tagged("은행권 가계대출 금리 인하"),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 2
    assert [item.article.cluster_size for item in representatives] == [1, 1]


def test_generic_macro_overlap_alone_does_not_cluster_unrelated_articles():
    tagged = [
        _tagged("원달러 환율 상승에 외환시장 변동성 확대", "거시·시장"),
        _tagged("항공업계 고환율 부담에 영업이익 감소", "거시·시장"),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 2


def test_cluster_representative_keeps_related_article_metadata():
    tagged = [
        _tagged("카카오뱅크 1분기 순이익 1873억", score=8),
        _tagged("카뱅, 1분기 순익 역대 최대", minutes=1, score=5),
    ]

    representative = cluster_tagged_articles(tagged)[0]

    assert representative.article.cluster_size == 2
    assert representative.article.related_count == 1
    assert representative.article.related_articles
    assert representative.article.related_articles[0]["title"]


def test_html_report_shows_cluster_count_badge_for_clustered_representative():
    representative = cluster_tagged_articles(
        [
            _tagged("카카오뱅크 1분기 순이익 1873억", score=8),
            _tagged("카뱅, 1분기 순익 역대 최대", minutes=1, score=5),
        ]
    )[0]

    html = render_html(datetime(2026, 5, 11), [representative], [])

    assert "카카오뱅크 1분기 순이익 1873억" in html
    assert "관련 기사 2건" in html


def test_cluster_handles_missing_optional_article_fields_without_crashing():
    tagged = [
        TaggedArticle(Article("", "", "", None, None, None, "test"), [], [], []),
        TaggedArticle(
            Article("카카오뱅크 1분기 순이익 1873억", "", "", None, None, None, "test"),
            ["은행"],
            [],
            [],
        ),
        TaggedArticle(
            Article("카뱅 1분기 순익 역대 최대", "", "", None, None, None, "test"),
            ["은행"],
            [],
            [],
        ),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 2
    assert max(item.article.cluster_size or 1 for item in representatives) == 2


def test_different_sector_articles_need_extremely_high_similarity_to_cluster():
    tagged = [
        _tagged("카카오뱅크 1분기 순이익 1873억", "은행"),
        _tagged("카뱅 1분기 순익 역대 최대", "보험"),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 2


def test_corporate_earnings_and_market_article_do_not_cluster_on_exchange_rate_only():
    tagged = [
        _tagged("제주항공 고환율 부담에 1분기 영업이익 감소", "여전"),
        _tagged("원달러 환율 상승에 외환시장 변동성 확대", "거시·시장"),
    ]

    representatives = cluster_tagged_articles(tagged)

    assert len(representatives) == 2
