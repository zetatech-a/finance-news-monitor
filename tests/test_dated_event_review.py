"""A shared reporting month must not bypass independent event safety."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/'+title}), ['보험'], [], [])


def pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster_features(ic._build_cluster_features(a), ic._build_cluster_features(b)) is merge
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


def first() -> TaggedArticle:
    return item('KB손보 2분기 킥스비율 200% 자본확충 완료')


@pytest.mark.parametrize('value,event', [
    ('200', '새 회계제도 대응 전략 발표'),
    ('201', '후순위채 발행'),
])
def test_same_month_disjoint_events_veto_before_metric_shortcut(value: str, event: str) -> None:
    a = first()
    b = item(f'KB손해보험 상반기 지급여력비율 {value}% {event}')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_subjects == fb.metric_subjects == {'kb손해보험'}
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert fb.reported_metrics == {('capital_adequacy_ratio', value)}
    assert fa.metric_period == fb.metric_period == (None, 6)
    assert not ic._conflicting_metric_periods(fa, fb)
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, False)


@pytest.mark.parametrize('period', ['상반기', '6월'])
@pytest.mark.parametrize('value', ['200.0', '201'])
def test_same_dated_event_keeps_spacing_and_value_tolerance(period: str, value: str) -> None:
    a = first()
    b = item(f'KB손보 {period} 지급여력비율 {value}% 자본 확충 완료')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_subjects == fb.metric_subjects == {'kb손해보험'}
    assert fa.metric_period == fb.metric_period == (None, 6)
    assert ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)


def test_dated_morphology_still_only_releases_veto() -> None:
    a = item('KB손보 2분기 킥스비율 200% 자본여력 하락')
    b = item('KB손보 상반기 지급여력비율 201% 자본여력은 하락했다')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)
    # No common exact value and no ordinary evidence: tolerance cannot authorize merging.
    assert not ic._should_cluster_features(*[
        replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)
    ])


@pytest.mark.parametrize('reverse', [False, True])
def test_one_sided_month_exception_preserves_ordinary_path(reverse: bool) -> None:
    a = first()
    b = item('KB손해보험 지급여력비율 200% 새 회계제도 대응 전략 발표')
    if reverse:
        a, b = b, a
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert {fa.metric_period, fb.metric_period} == {(None, 6), (None, None)}
    assert not ic._conflicting_metric_periods(fa, fb)
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, False)  # Existing ordinary evidence is insufficient; no forced merge.
    assert not ic._should_cluster_features(*[
        replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)
    ])  # No same-period shortcut with one month absent.


def test_real_reporting_period_conflict_still_wins() -> None:
    a = item('KB손보 1분기 킥스비율 200% 자본확충 완료')
    b = item('KB손해보험 6월 지급여력비율 201% 자본 확충 완료')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_period == (None, 3) and fb.metric_period == (None, 6)
    assert ic._conflicting_metric_periods(fa, fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, False)


@pytest.mark.parametrize('ending', ['...', '…'])
def test_dated_truncated_wire_stays_conservative(ending: str) -> None:
    a = item('KB손보 2분기 킥스비율 200% 자본확'+ending)
    b = item('KB손해보험 상반기 지급여력비율 200% 자본확충 완료')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.headline_is_truncated
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)


def test_same_month_bare_wire_cannot_bridge_disjoint_events() -> None:
    a = first()
    b = item('KB손해보험 6월 지급여력비율 200%')
    c = item('KB손해보험 상반기 지급여력비율 200% 새 회계제도 대응 전략 발표')
    assert ic._should_cluster(a, b) and ic._should_cluster(b, c)
    for order in permutations([a, b, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id


def test_one_sided_month_genuine_wire_keeps_existing_merge() -> None:
    a = first()
    b = item('KB손보 지급여력비율 200% 자본 확충 완료')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_period == (None, 6) and fb.metric_period == (None, None)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)


def test_exact_alias_same_dated_event_wire() -> None:
    a = first()
    b = item('KB손해보험 상반기 지급여력비율 200.0% 자본 확충 완료')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_subjects == fb.metric_subjects == {'kb손해보험'}
    assert fa.metric_period == fb.metric_period == (None, 6)
    assert ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)


@pytest.mark.parametrize('month', ['3월', '6월'])
def test_reporting_month_is_not_residual_event_evidence(month: str) -> None:
    feature = ic._build_cluster_features(item(f'KB손보 {month} 지급여력비율 200%'))
    assert not ic._metric_event_tokens(feature)
    assert not ic._metric_event_comparison_units(feature)


@pytest.mark.parametrize('title,token', [
    ('KB손보 지급여력비율 200% 6월 전망', '6월'),
    ('KB손보 6월 행사', '6월'),
])
def test_bare_month_residual_mask_is_measurement_prefix_only(title: str, token: str) -> None:
    feature = ic._build_cluster_features(item(title))
    assert token in ic._metric_event_tokens(feature)


@pytest.mark.parametrize('left,right', [
    ('전 분기比 0.8%p↓', '요구자본 증가'),
    ('요구자본 증가', '3개월 새 0.8%p 하락'),
    ('주식투자 위험 증가 영향', '소폭 하락'),
])
def test_dated_statistical_framing_is_not_an_independent_announcement(left: str, right: str) -> None:
    a = item('보험사 6월말 킥스비율 215.2% '+left)
    b = item('보험사 2분기 지급여력비율 215.2% '+right)
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)
    # Removing the fact and ordinary evidence also removes merge authorization.
    assert not ic._should_cluster_features(*[
        replace(f, reported_metrics=set(), issue_terms=set(), entities=set(), numbers=set())
        for f in (fa, fb)
    ])


@pytest.mark.parametrize('value', ['200.00', '201'])
def test_dated_explicit_success_and_policy_assertions_remain_distinct(value: str) -> None:
    a = item('KB손보 2분기 킥스비율 200% 요구자본 확충 성공')
    b = item(f'KB손해보험 6월말 지급여력비율 {value}% 새 회계제도 대응 여력 입증')
    assert ic._metric_match_lacks_event_evidence(ic._build_cluster_features(a), ic._build_cluster_features(b))
    pair(a, b, False)


def test_dated_announcement_lexical_continuation_not_action_assertion() -> None:
    a = item('보험사 2분기 킥스비율 200% 발표자료')
    b = item('보험사 상반기 지급여력비율 200% 계획서')
    assert not ic._metric_match_lacks_event_evidence(ic._build_cluster_features(a), ic._build_cluster_features(b))
