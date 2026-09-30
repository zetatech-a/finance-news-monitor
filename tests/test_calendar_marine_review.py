"""Calendar dates are not monthly snapshots; marine insurers are subjects."""
from dataclasses import replace

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title):
    return TaggedArticle(make_article({'title': title, 'summary': '',
                                      'url': 'https://example.test/' + title}), ['보험'], [], [])


def pair(a, b, merge):
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


@pytest.mark.parametrize('day', [1, 17, 30, 31])
def test_calendar_day_does_not_supply_month(day):
    a = item(f'9월 {day}일 삼성생명 킥스비율 200% 자본여력 감소')
    b = item('삼성생명 6월말 지급여력비율 200% 자본여력 감소')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert fa.metric_period == (None, None)
    assert fb.metric_period == (None, 6)
    assert not ic._conflicting_metric_periods(fa, fb)
    pair(a, b, True)


def test_calendar_date_preserves_real_snapshot():
    a = item('9월 17일 삼성생명 6월말 킥스비율 200% 자본여력 감소')
    b = item('삼성생명 2분기 지급여력비율 200% 자본여력 감소')
    assert ic._build_cluster_features(a).metric_period == (None, 6)
    pair(a, b, True)


@pytest.mark.parametrize('prefix,month', [
    ('3월', 3), ('3월 기준', 3), ('6월 기준', 6), ('1분기', 3),
    ('2분기', 6), ('상반기', 6), ('6월말', 6), ('6월 말', 6),
    ('3월물', None), ('3월호', None), ('3월분기', None), ('13월', None),
    ('3월abc', None), ('3월1', None), ('3월 6월', None),
    ('3월 대비 6월 기준', 6), ('9월 17일정', 9), ('9월 32일', 9),
])
def test_bounded_calendar_exclusion_preserves_existing_month_grammar(prefix, month):
    assert ic._metric_period(ic._normalize_title(f'삼성생명 {prefix} KICS 200%')) == (None, month)


def test_real_month_conflict_still_splits():
    a = item('삼성생명 3월 킥스비율 200% 자본여력 감소')
    b = item('삼성생명 6월 지급여력비율 201% 자본여력 감소')
    assert ic._conflicting_metric_periods(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)


def test_calendar_exclusion_is_metric_local_and_prefix_only():
    title = '9월 17일 서울시 소상공인 불법대부 집중 단속'
    # Enforcement now independently excludes the same day-qualified calendar date.
    assert ic._enforcement_period(ic._normalize_title(title)) == (None, None)
    assert '17일' in ic._normalize_title(title)
    assert ic._metric_period('삼성생명 킥스비율 200% 6월 전망') == (None, None)


def test_marine_insurer_subject_veto_prevents_cross_company_merge():
    a = item('현대해상 킥스 20% 건전성 하락')
    b = item('한화생명 지급여력비율 20% 건전성 하락')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '20')}
    assert fa.metric_subjects == {'현대해상'}
    assert fb.metric_subjects == {'한화생명'}
    assert ic._conflicting_metric_subjects(fa, fb)
    pair(a, b, False)


def test_same_marine_insurer_wire_and_local_scope():
    a = item('현대해상 킥스 20% 자본여력 감소')
    b = item('현대해상 지급여력비율 20.0% 자본여력 감소')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_subjects == fb.metric_subjects == {'현대해상'}
    assert not ic._conflicting_metric_subjects(fa, fb)
    assert '현대해상' not in ic._extract_entities(a.article.title)
    pair(a, b, True)
    assert not ic._should_cluster_features(*[
        replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)
    ])  # Recognition does not add a new merge shortcut.


@pytest.mark.parametrize('title', [
    '현대해상이익 킥스 20%', '킥스 20% 현대해상 발표',
])
def test_marine_subject_boundary_and_prefix_ownership(title):
    assert not ic._metric_subjects(ic._normalize_title(title))


def test_calendar_date_and_marine_subject_interaction():
    a = item('9월 17일 현대해상 킥스 20% 자본여력 감소')
    b = item('현대해상 6월말 지급여력비율 20.0% 자본여력 감소')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_period == (None, None)
    assert fa.metric_subjects == fb.metric_subjects == {'현대해상'}
    assert not ic._conflicting_metric_periods(fa, fb)
    pair(a, b, True)
