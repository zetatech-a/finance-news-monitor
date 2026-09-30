"""Industry subjects retain identity across bounded Korean particles."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def tagged(title: str) -> TaggedArticle:
    # Use the same sector to exercise similarity independently of sector tags.
    return TaggedArticle(make_article({
        "title": title, "summary": "", "url": "https://example.test/" + title,
    }), ["은행"], [], [])


@pytest.mark.parametrize("prefix,subject", [
    ("은행권은", "은행권"), ("은행권이", "은행권"),
    ("저축은행권은", "저축은행권"), ("저축은행권이", "저축은행권"),
    ("보험사는", "보험사"), ("보험사가", "보험사"), ("보험사의", "보험사"),
    ("생보사는", "생보사"), ("손보사가", "손보사"), ("카드사는", "카드사"),
    ("은행권들", "은행권"), ("은행권들은", "은행권"), ("저축은행권들이", "저축은행권"),
    ("은행권", "은행권"), ("은행권은,", "은행권"),
])
def test_industry_subject_particles(prefix: str, subject: str) -> None:
    assert ic._metric_subjects(ic._normalize_title(f"{prefix} 연체율 10% 상승")) == {subject}


@pytest.mark.parametrize("particle", ["은", "이"])
@pytest.mark.parametrize("check", ["conflict", "pair", "cluster"])
def test_different_banking_scopes_do_not_merge(particle: str, check: str) -> None:
    a = tagged(f"은행권{particle} 연체율 10% 상승")
    b = tagged(f"저축은행권{particle} 연체율 10% 상승")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert fa.reported_metrics == fb.reported_metrics == {("delinquency_rate", "10")}
    # Missing subjects expose the ordinary-similarity false merge being fixed.
    assert ic._should_cluster_features(replace(fa, metric_subjects=set()),
                                       replace(fb, metric_subjects=set()))
    if check == "conflict":
        assert ic._conflicting_metric_subjects(fa, fb)
    elif check == "pair":
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("subject", ["은행권", "저축은행권"])
def test_same_scope_particle_variants_keep_wire_cluster(subject: str) -> None:
    a = tagged(f"{subject}은 연체율 10% 상승")
    b = tagged(f"{subject}이 연체율 10.0% 상승")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert fa.metric_subjects == fb.metric_subjects == {subject}
    assert fa.reported_metrics == fb.reported_metrics == {("delinquency_rate", "10")}
    assert not ic._conflicting_metric_subjects(fa, fb)
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


@pytest.mark.parametrize("prefix", [
    "은행권이익", "저축은행권역", "보험사가치", "은행권들러리",
    "은행권의견", "은행권은퇴", "은행권이abc", "저축은행권은123",
    "은행권abc", "저축은행권123",
])
def test_lexical_continuation_is_not_an_industry_subject(prefix: str) -> None:
    assert not ic._metric_subjects(ic._normalize_title(f"{prefix} 연체율 10% 상승"))


@pytest.mark.parametrize("particle", ["은", "이"])
def test_scope_conflict_cannot_bridge_through_missing_subject(particle: str) -> None:
    a = tagged(f"은행권{particle} 연체율 10% 상승 통계 발표")
    bridge = tagged("연체율 10% 상승 통계 발표")
    c = tagged(f"저축은행권{particle} 연체율 10% 상승 통계 발표")
    assert not ic._build_cluster_features(bridge).metric_subjects
    assert ic._should_cluster(a, bridge) and ic._should_cluster(bridge, c)
    for order in permutations([a, bridge, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id
