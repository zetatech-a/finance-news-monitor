"""Comparison-only compound spacing and explicit prefix-month regressions."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(event: str = '', period: str = '', label: str = '킥스비율', value: str = '200') -> TaggedArticle:
    title = f'삼성생명 {period} {label} {value}% {event}'
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/'+title}), ['보험'], [], [])


def features(a: TaggedArticle, b: TaggedArticle) -> tuple[ic._ClusterFeatures, ic._ClusterFeatures]:
    return ic._build_cluster_features(a), ic._build_cluster_features(b)


def assert_pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


def test_codex_compound_spacing() -> None:
    a, b = item('자본확충'), item('자본 확충', label='지급여력비율')
    fa, fb = features(a, b)
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert ic._metric_event_tokens(fa) == {'자본확충'}
    assert ic._metric_event_tokens(fb) == {'자본', '확충'}
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    assert_pair(a, b, True)


@pytest.mark.parametrize('compact,spaced', [
    ('요구자본', '요구 자본'),
    ('자본확충', '자본 확충은'),
    ('자본확충 하락', '자본 확충은 하락했다'),
])
def test_compounds_and_existing_morphology(compact: str, spaced: str) -> None:
    a, b = item(compact), item(spaced, label='지급여력비율')
    assert not ic._metric_match_lacks_event_evidence(*features(a, b))
    assert_pair(a, b, True)


@pytest.mark.parametrize('compact,spaced', [
    ('자본확충', '자본 별도 확충'),
    ('새회계제도', '새 회계제도'),
    ('자본확충', '자본 (별도) 확충'),
    ('자본확충', '자본. 확충'),
    ('자본확충', '자본 발표 확충'),
    ('자본확충추진', '자본 확충 추진'),
])
def test_only_adjacent_retained_two_syllable_words(compact: str, spaced: str) -> None:
    a, b = item(compact), item(spaced, label='지급여력비율')
    assert ic._metric_match_lacks_event_evidence(*features(a, b))
    assert_pair(a, b, False)


def test_removed_metric_does_not_create_adjacency() -> None:
    a, b = item('자본확충'), item('확충', label='자본 지급여력비율')
    assert ic._metric_match_lacks_event_evidence(*features(a, b))
    assert_pair(a, b, False)


def test_compound_overlap_neither_shortcut_nor_global_change() -> None:
    a, b = item('자본확충'), item('자본 확충', label='지급여력비율')
    fa, fb = features(a, b)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    stripped = [replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)]
    assert not ic._should_cluster_features(*stripped)
    assert '자본확충' not in fb.tokens
    assert ic._tokenize_title(b.article.title) == fb.tokens


@pytest.mark.parametrize('left,right', [
    ('자본확충 완료', '새 회계제도 대응 전략 발표'),
    ('후순위채 발행', '회계제도 대응 계획'),
])
def test_distinct_events_still_veto(left: str, right: str) -> None:
    a, b = item(left), item(right, label='지급여력비율')
    assert ic._metric_match_lacks_event_evidence(*features(a, b))
    assert_pair(a, b, False)


def test_bare_metric_bridge_permutations() -> None:
    a, bridge, c = item('자본확충 완료'), item(label='지급여력비율'), item('새 회계제도 대응 전략 발표', label='지급여력비율')
    for order in permutations([a, bridge, c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_codex_different_bare_months_and_values() -> None:
    a = item('자본여력 감소', period='3월')
    b = item('자본여력 감소', period='6월', label='지급여력비율', value='201')
    fa, fb = features(a, b)
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.metric_period == (None, 3)
    assert fb.metric_period == (None, 6)
    assert ic._conflicting_metric_periods(fa, fb)
    assert_pair(a, b, False)


@pytest.mark.parametrize('month', range(1, 13))
@pytest.mark.parametrize('suffix', ['', ' 기준'])
def test_bounded_bare_months(month: int, suffix: str) -> None:
    f = ic._build_cluster_features(item(period=f'{month}월{suffix}', label='KICS'))
    assert f.metric_period == (None, month)


@pytest.mark.parametrize('period', ['3월물', '3월호', '3월분기', '13월', '3월abc', '3월1'])
def test_non_month_lexical_continuations(period: str) -> None:
    assert ic._build_cluster_features(item(period=period)).metric_period == (None, None)


@pytest.mark.parametrize('left,right,month', [
    ('1분기', '3월', 3), ('2분기', '6월', 6), ('3분기', '9월', 9),
    ('4분기', '12월', 12), ('상반기', '6월', 6), ('2분기', '6월말', 6),
    ('2분기', '6월 말', 6), ('3월', '3월', 3),
])
def test_period_equivalent_wire(left: str, right: str, month: int) -> None:
    a, b = item('자본여력 개선', left), item('개선', right, '지급여력비율', '200.0')
    fa, fb = features(a, b)
    assert fa.metric_period == fb.metric_period == (None, month)
    assert not ic._conflicting_metric_periods(fa, fb)
    assert_pair(a, b, True)


def test_missing_or_ambiguous_month_is_not_conflict() -> None:
    a = item(period='3월')
    for period in ('', '3월 6월'):
        b = item(period=period, label='지급여력비율')
        fa, fb = features(a, b)
        assert fa.metric_period == (None, 3)
        assert fb.metric_period == (None, None)
        assert not ic._conflicting_metric_periods(fa, fb)
        assert_pair(a, b, True)


@pytest.mark.parametrize('prefix,event,expected', [
    ('3월', '6월 전망도 발표', (None, 3)),
    ('', '3월 대비 개선', (None, None)),
    ('2026년 3월', '', (2026, 3)),
    ('3월 6월', '', (None, None)),
])
def test_prefix_only_and_unique_dimensions(prefix: str, event: str, expected: tuple[int | None, int | None]) -> None:
    assert ic._build_cluster_features(item(event, prefix, 'KICS')).metric_period == expected


@pytest.mark.parametrize('right_month,merge', [('3월', True), ('6월', False)])
def test_period_veto_has_priority_over_compound_equivalence(right_month: str, merge: bool) -> None:
    a, b = item('자본확충', '3월'), item('자본 확충', right_month, '지급여력비율', '200.0')
    assert ic._conflicting_metric_periods(*features(a, b)) is (not merge)
    assert_pair(a, b, merge)


def test_bare_month_statistical_inflections() -> None:
    a = item('자본여력 감소', '3월', 'KICS')
    b = item('자본여력은 감소했다', '3월', '지급여력비율', '200.0')
    assert_pair(a, b, True)


def test_month_conflict_bridge_all_orders() -> None:
    a, bridge, c = item('자본여력 감소', '3월'), item('자본여력 감소'), item('자본여력 감소', '6월')
    for order in permutations([a, bridge, c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id