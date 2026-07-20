from __future__ import annotations

from datetime import datetime, timedelta

from src.config import KST
from src.pipeline.dedup import deduplicate
from src.pipeline.issue_cluster import cluster_tagged_articles
from src.pipeline.normalize import Article
from src.pipeline.tagger import TaggedArticle

BASE = datetime(2026, 7, 15, 9, 0, tzinfo=KST)


def _article(title: str, link: str, minutes: int = 0) -> Article:
    return Article(
        title=title,
        description=f"{title} 요약",
        link=link,
        originallink=link,
        naver_link=None,
        pub_date=BASE - timedelta(minutes=minutes),
        query="q",
    )


def _tagged(article: Article, sector: str = "저축은행") -> TaggedArticle:
    article.relevance_score = 7
    return TaggedArticle(article=article, sectors=[sector], topics=[], matched_keywords=[])


def test_dedup_records_absorbed_sources_on_representative():
    articles = [
        _article("저축은행 연체율 8% 돌파", "https://a.com/1", minutes=0),
        _article("저축은행 연체율 8% 돌파", "https://b.com/2", minutes=10),
        _article("저축은행 연체율 8% 돌파 [종합]", "https://c.com/3", minutes=20),
    ]

    unique = deduplicate(articles)

    assert len(unique) == 1
    rep = unique[0]
    absorbed_links = {d["link"] for d in rep.duplicate_sources}
    assert len(rep.duplicate_sources) == 2
    assert rep.link not in absorbed_links  # 대표 자신은 목록에 없음


def test_cluster_size_and_related_include_absorbed_duplicates():
    # dedup을 거친 대표 1건 + 흡수분 2건, 그리고 변형 제목 1건이 이슈로 묶이는 상황
    articles = [
        _article("저축은행 연체율 8% 돌파…금감원 현장점검", "https://a.com/1", minutes=0),
        _article("저축은행 연체율 8% 돌파…금감원 현장점검", "https://b.com/2", minutes=10),
        _article("[속보] 저축은행 연체율 8% 돌파…금감원 현장점검", "https://c.com/3", minutes=20),
        _article("연체율 치솟은 저축은행…금감원 현장점검 나선다", "https://d.com/4", minutes=30),
    ]
    unique = deduplicate(articles)
    tagged = [_tagged(a) for a in unique]

    reps = cluster_tagged_articles(tagged)

    assert len(reps) == 1
    rep = reps[0].article
    # 멤버 2건(대표+변형) + 흡수 2건 = 총 4건
    assert rep.cluster_size == 4
    assert rep.related_count == 3
    related_links = [r["link"] for r in rep.related_articles]
    all_links = {"https://a.com/1", "https://b.com/2", "https://c.com/3", "https://d.com/4"}
    # 어떤 기사가 대표가 되든, 관련 목록은 '대표를 제외한 전부'여야 한다
    # (issue_cluster 멤버 + dedup 흡수분 모두 포함)
    assert rep.link in all_links
    assert set(related_links) == all_links - {rep.link}


def test_single_member_cluster_with_duplicates_gets_badge_counts():
    # 변형 제목 없이 같은 제목 보도만 3건 — 예전에는 관련 기사 0건으로 보였다
    articles = [
        _article("카드론 연체율 경고등", "https://a.com/1", minutes=0),
        _article("카드론 연체율 경고등", "https://b.com/2", minutes=10),
        _article("카드론 연체율 경고등", "https://c.com/3", minutes=20),
    ]
    unique = deduplicate(articles)
    reps = cluster_tagged_articles([_tagged(a, "여전") for a in unique])

    rep = reps[0].article
    assert rep.cluster_size == 3
    assert rep.related_count == 2
    assert len(rep.related_articles) == 2


def test_related_articles_capped_at_five():
    articles = [
        _article("새마을금고 건전성 점검", f"https://s{i}.com/x", minutes=i) for i in range(9)
    ]
    unique = deduplicate(articles)
    reps = cluster_tagged_articles([_tagged(a, "상호금융") for a in unique])

    rep = reps[0].article
    assert rep.cluster_size == 9
    assert rep.related_count == 8
    assert len(rep.related_articles) == 5  # 저장 상한 유지
