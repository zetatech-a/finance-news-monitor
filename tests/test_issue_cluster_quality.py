from __future__ import annotations

from datetime import datetime, timedelta

from src.pipeline.issue_cluster import cluster_tagged_articles
from src.pipeline.normalize import Article
from src.pipeline.tagger import TaggedArticle


def _article(title: str, *, minutes: int = 0, score: int = 5) -> Article:
    article = Article(
        title=title,
        description="요약",
        link=f"https://example.com/{minutes}",
        originallink=None,
        naver_link=f"https://n.news.naver.com/{minutes}",
        pub_date=datetime(2026, 6, 23, 9, 0) + timedelta(minutes=minutes),
        query="test",
    )
    article.relevance_score = score
    return article


def _tagged(title: str, sector: str = "감독·제재", *, minutes: int = 0, score: int = 5) -> TaggedArticle:
    return TaggedArticle(_article(title, minutes=minutes, score=score), [sector], [], [])


def _groups(reps: list[TaggedArticle]) -> list[set[str]]:
    groups = []
    for rep in reps:
        titles = {rep.article.title}
        titles.update(item["title"] for item in (rep.article.related_articles or []))
        groups.append(titles)
    return groups


def _has_group(reps: list[TaggedArticle], *titles: str) -> bool:
    wanted = set(titles)
    return any(wanted.issubset(group) for group in _groups(reps))


def test_savings_bank_inspection_variants_cluster():
    reps = cluster_tagged_articles([
        _tagged("금감원, 저축은행 검사 착수"),
        _tagged("금융감독원 저축은행 현장점검 확대", minutes=1),
    ])
    assert len(reps) == 1
    assert reps[0].article.cluster_size == 2


def test_illegal_collection_sns_threat_variants_cluster():
    reps = cluster_tagged_articles([
        _tagged("SNS 얼굴 박제 불법추심 피해 확산", "대부"),
        _tagged("불법추심 피해자 SNS 협박 잇따라", "대부", minutes=1),
    ])
    assert len(reps) == 1


def test_savings_bank_deposit_rate_variants_cluster():
    reps = cluster_tagged_articles([
        _tagged("저축은행 예금금리 4%대 재진입", "은행"),
        _tagged("저축은행 수신금리 다시 4%대", "은행", minutes=1),
    ])
    assert len(reps) == 1


def test_illegal_loan_ad_crackdown_variants_cluster():
    reps = cluster_tagged_articles([
        _tagged("금융위, SNS 불법 대부광고 단속", "입법·정책"),
        _tagged("금융당국 불법 대출광고 특별단속", "입법·정책", minutes=1),
    ])
    assert len(reps) == 1


def test_card_loan_delinquency_variants_cluster():
    reps = cluster_tagged_articles([
        _tagged("카드론 연체율 상승 경고등", "여전"),
        _tagged("카드론 연체율 또 상승", "여전", minutes=1),
    ])
    assert len(reps) == 1


def test_regulator_generic_terms_do_not_merge_different_targets():
    reps = cluster_tagged_articles([
        _tagged("금감원 저축은행 검사 착수"),
        _tagged("금감원 보험사 불완전판매 제재", "보험", minutes=1),
    ])
    assert len(reps) == 2
    assert not _has_group(reps, "금감원 저축은행 검사 착수", "금감원 보험사 불완전판매 제재")


def test_savings_bank_rate_and_bad_loan_do_not_cluster():
    reps = cluster_tagged_articles([
        _tagged("저축은행 예금금리 4%대 재진입", "은행"),
        _tagged("저축은행 부실채권 연체율 상승", "은행", minutes=1),
    ])
    assert len(reps) == 2


def test_macro_market_generic_rise_close_does_not_cluster():
    reps = cluster_tagged_articles([
        _tagged("원달러 환율 상승 마감", "거시·시장"),
        _tagged("코스피 상승 마감", "거시·시장", minutes=1),
    ])
    assert len(reps) == 2


def test_low_value_briefing_and_social_campaign_do_not_cluster():
    reps = cluster_tagged_articles([
        _tagged("금융 브리핑 NH농협은행·신협중앙회·새마을금고", "은행"),
        _tagged("은행권 사회공헌 캠페인 확대", "은행", minutes=1),
    ])
    assert len(reps) == 2


def test_schedule_article_does_not_cluster_with_hard_news():
    reps = cluster_tagged_articles([
        _tagged("다음주 한국은행 및 금융위·금감원 일정", "입법·정책"),
        _tagged("금감원 저축은행 검사 착수", "감독·제재", minutes=1),
    ])
    assert len(reps) == 2


def test_cluster_metadata_is_deterministic_and_preserves_tagged_articles():
    tagged = [
        _tagged("카드론 연체율 상승 경고등", "여전", score=7),
        _tagged("카드론 연체율 또 상승", "여전", minutes=1, score=6),
        _tagged("원달러 환율 상승 마감", "거시·시장", minutes=2),
    ]
    reps1 = cluster_tagged_articles(tagged)
    ids1 = sorted((rep.article.title, rep.article.cluster_id, rep.article.cluster_size) for rep in reps1)
    reps2 = cluster_tagged_articles(list(tagged))
    ids2 = sorted((rep.article.title, rep.article.cluster_id, rep.article.cluster_size) for rep in reps2)

    assert ids1 == ids2
    assert len(reps1) == 2
    assert sorted(rep.article.cluster_size for rep in reps1) == [1, 2]
    assert all(isinstance(rep, TaggedArticle) for rep in reps1)
    assert all(rep.article.title for rep in reps1)


def test_empty_and_single_item_inputs_have_sane_cluster_metadata():
    assert cluster_tagged_articles([]) == []

    reps = cluster_tagged_articles([_tagged("카드론 연체율 상승 경고등", "여전")])

    assert len(reps) == 1
    assert reps[0].article.cluster_id.startswith("issue-")
    assert reps[0].article.cluster_size == 1
    assert reps[0].article.related_count == 0
    assert reps[0].article.related_articles == []


def test_generic_finance_terms_do_not_create_mega_cluster():
    reps = cluster_tagged_articles([
        _tagged("금융위 정책 발표", "입법·정책"),
        _tagged("금감원 검사 결과 발표", "감독·제재", minutes=1),
        _tagged("은행권 대출 금리 상승", "은행", minutes=2),
        _tagged("금융시장 환율 증시 동반 상승", "거시·시장", minutes=3),
    ])

    assert len(reps) == 4
    assert all(rep.article.cluster_size == 1 for rep in reps)


def test_deterministic_cluster_ids_across_reversed_input_order():
    tagged = [
        _tagged("금감원, 저축은행 검사 착수", "감독·제재", score=9),
        _tagged("금융감독원 저축은행 현장점검 확대", "감독·제재", minutes=1, score=7),
        _tagged("금감원 보험사 불완전판매 제재", "보험", minutes=2, score=8),
    ]

    forward = cluster_tagged_articles(tagged)
    reverse = cluster_tagged_articles(list(reversed(tagged)))

    forward_ids = sorted((rep.article.title, rep.article.cluster_id, rep.article.cluster_size) for rep in forward)
    reverse_ids = sorted((rep.article.title, rep.article.cluster_id, rep.article.cluster_size) for rep in reverse)
    assert forward_ids == reverse_ids
