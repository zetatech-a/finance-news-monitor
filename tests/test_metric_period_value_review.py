"""Period safety compares metric identities, not their reported values."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def tagged(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({
        "title": title, "summary": "", "url": "https://example.test/" + title,
    }), ["보험"], [], [])


@pytest.mark.parametrize("left,right", [
    ("1분기 킥스비율 200%", "2분기 킥스비율 201%"),
    ("2025년 4분기 킥스비율 200%", "2026년 4분기 킥스비율 203%"),
])
@pytest.mark.parametrize("check", ["period", "pair", "cluster"])
def test_different_period_and_value_cannot_merge(left: str, right: str, check: str) -> None:
    a = tagged(f"KB손해보험 {left} 자본확충 완료")
    b = tagged(f"KB손해보험 {right} 자본확충 완료")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert not (fa.reported_metrics & fb.reported_metrics)
    assert fa.metric_subjects == fb.metric_subjects == {"kb손해보험"}
    if check == "period":
        assert ic._conflicting_metric_periods(fa, fb)
    elif check == "pair":
        # Establish the ordinary-similarity path that the safety veto must block.
        assert ic._should_cluster_features(replace(fa, metric_period=(None, None)), fb)
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("left,right", [
    ("2분기", "2분기"), ("2분기", "6월말"), ("상반기", "6월말"), ("", "2분기"),
])
def test_same_equivalent_or_missing_period_does_not_veto_different_values(left: str, right: str) -> None:
    a = tagged(f"KB손해보험 {left} 킥스비율 200%")
    b = tagged(f"KB손보 {right} K-ICS 비율 201%")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert not (fa.reported_metrics & fb.reported_metrics)
    assert not ic._conflicting_metric_periods(fa, fb)
    # Removing measurement evidence must not change the ordinary-similarity result.
    assert ic._should_cluster(a, b) == ic._should_cluster_features(
        replace(fa, reported_metrics=set()), replace(fb, reported_metrics=set()),
    )


def test_different_metric_identities_do_not_create_period_conflict() -> None:
    a = ic._build_cluster_features(tagged("KB손해보험 1분기 연체율 3%"))
    b = ic._build_cluster_features(tagged("KB손해보험 2분기 예대금리차 3%"))
    assert a.reported_metrics == {("delinquency_rate", "3")}
    assert b.reported_metrics == {("loan_deposit_spread", "3")}
    assert a.metric_subjects == b.metric_subjects == {"kb손해보험"}
    assert not ic._conflicting_metric_periods(a, b)


def test_different_value_does_not_authorize_exact_metric_shortcut() -> None:
    a = tagged("KB손보 2분기 킥스비율 200% 요구자본 확충 성공")
    b = tagged("KB손해보험 6월말 지급여력비율 201% 새 회계제도 대응 여력 입증")
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2
    same_value = tagged(b.article.title.replace("201%", "200.00%"))
    assert ic._should_cluster(a, same_value)
    assert len(ic.cluster_tagged_articles([a, same_value])) == 1


def test_same_quarter_wires_stay_separate_from_next_quarter() -> None:
    a = tagged("KB손해보험 1분기 킥스비율 200% 자본확충 완료")
    b = tagged("KB손보 1분기 K-ICS 비율 200.0% 자본확충 마무리")
    c = tagged("KB손해보험 2분기 킥스비율 201% 자본확충 완료")
    for order in permutations([a, b, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id == b.article.cluster_id != c.article.cluster_id


def test_different_value_period_conflict_cannot_bridge_through_missing_period() -> None:
    a = tagged("KB손해보험 1분기 킥스비율 200% 자본확충 완료")
    bridge = tagged("KB손해보험 킥스비율 201% 자본확충 완료")
    c = tagged("KB손해보험 2분기 킥스비율 201% 자본확충 완료")
    assert ic._should_cluster(a, bridge) and ic._should_cluster(bridge, c)
    for order in permutations([a, bridge, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id
