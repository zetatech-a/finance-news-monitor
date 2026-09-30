"""Bounded campaign dates, aggregate periods, actor provenance and year reports."""
from __future__ import annotations

from dataclasses import replace
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str, sector: str = '보험', summary: str = '') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': summary,
                                      'url': 'https://example.test/' + title}), [sector], [], [])


def pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


def test_a_calendar_dates_are_not_campaign_months() -> None:
    a = item('9월 17일 서울시 소상공인 불법사금융 특별 단속 착수', '대부')
    b = item('10월 1일 서울시 소상공인 불법사금융 특별 단속 착수', '대부')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert ic._is_enforcement_headline(fa.norm_title)
    assert fa.fingerprint == fb.fingerprint == 'enforcement:서울시:small_business'
    assert fa.enforcement_period == fb.enforcement_period == (None, None)
    assert not ic._conflicting_enforcement_periods(fa, fb)
    pair(a, b, True)


@pytest.mark.parametrize('prefix,expected', [
    ('9월', (None, 9)), ('9월말', (None, 9)), ('2026년 9월', (2026, 9)),
    ('9월 30일', (None, None)), ('10월 31일', (None, None)),
    ('9월 17일 6월', (None, 6)), ('9월 32일', (None, 9)),
    ('9월 17일정', (None, 9)),
])
def test_a_bounded_calendar_grammar(prefix: str, expected: tuple) -> None:
    assert ic._enforcement_period(prefix + ' 서울시 불법대부 특별 단속') == expected


def test_a_real_campaign_period_conflict_and_missingness() -> None:
    a = item('서울시 3월 소상공인 불법대부 특별 단속', '대부')
    b = item('서울시 9월 소상공인 불법대부 특별 단속', '대부')
    assert ic._conflicting_enforcement_periods(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)
    pair(a, item('서울시 소상공인 불법대부 특별 단속', '대부'), True)


@pytest.mark.parametrize('period', ['2분기', '상반기', '6월', '6월말', '6월 말', '2026년 2분기'])
def test_b_aggregate_owns_measurement_across_bounded_period(period: str) -> None:
    a = item(f'삼성생명 등 10개 보험사 {period} 킥스비율 215.2% 자본여력 감소')
    b = item(f'10개 보험사 {period} 지급여력비율 215.2% 자본여력 감소')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_subjects == fb.metric_subjects == {'보험사'}
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '215.2')}
    assert fa.metric_period == fb.metric_period == (2026 if '2026' in period else None, 6)
    assert not ic._conflicting_metric_subjects(fa, fb)
    pair(a, b, True)


@pytest.mark.parametrize('prefix,subjects', [
    ('삼성생명 등 10개 보험사', {'보험사'}),
    ('삼성생명 등 보험사 관련 2분기', {'삼성생명'}),
    ('보험사 중 삼성생명 2분기', {'삼성생명'}),
    ('삼성생명과 한화생명 2분기', {'삼성생명', '한화생명'}),
    ('삼성생명 등 생보사 상반기', {'생보사'}),
    ('KB손해보험을 포함한 손보사 6월 말', {'손보사'}),
    ('삼성생명 등 보험사 6월호', {'삼성생명'}),
])
def test_b_no_arbitrary_bridge_or_scope_collapse(prefix: str, subjects: set[str]) -> None:
    assert ic._metric_subjects(ic._normalize_title(prefix + ' 킥스비율 215.2%')) == subjects


def test_b_aggregate_still_obeys_period_conflict() -> None:
    a = item('삼성생명 등 10개 보험사 1분기 킥스비율 215.2% 자본여력 감소')
    b = item('보험사 2분기 지급여력비율 215.2% 자본여력 감소')
    assert ic._conflicting_metric_periods(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)


@pytest.mark.parametrize('actor', ['금감원', '금융감독원', '금융위', '금융위원회'])
def test_c_explicit_regulator_does_not_borrow_local_actor(actor: str) -> None:
    a = item(actor + ' 소상공인 불법사금융 특별 단속', '대부', '서울시 피해지원 제도 사례를 소개했다')
    b = item('서울시 전통시장 불법대부 특별 수사', '대부')
    assert ic._is_enforcement_headline(ic._normalize_issue_text(a.article.title))
    assert not ic._LOCAL_AUTHORITY_RE.findall(ic._normalize_issue_text(a.article.title))
    assert ic._LOCAL_AUTHORITY_RE.findall(ic._normalize_issue_text(a.article.description)) == ['서울시']
    assert ic._issue_fingerprint(a) is None
    pair(a, b, False)


def test_c_title_local_authority_wins_over_background() -> None:
    a = item('서울시 소상공인 불법사금융 특별 단속', '대부', '부산시 피해지원 제도 사례를 소개했다')
    b = item('서울시 전통시장 불법대부 특별 수사', '대부')
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b) == 'enforcement:서울시:small_business'
    pair(a, b, True)


def test_c_genuinely_missing_actor_and_target_still_use_description() -> None:
    a = item('불법사금융 특별 단속', '대부', '서울시 전통시장 소상공인을 대상으로 한다')
    b = item('서울시 전통시장 불법대부 특별 수사', '대부')
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b) == 'enforcement:서울시:small_business'
    pair(a, b, True)


def test_c_ambiguous_title_actors_not_resolved_by_description() -> None:
    a = item('서울시 부산시 소상공인 불법대부 특별 단속', '대부', '서울시 사례')
    assert ic._issue_fingerprint(a) is None


def test_d_year_only_statistical_framing_is_dated() -> None:
    a = item('삼성생명 2025년 킥스비율 200% 자본여력 감소')
    b = item('삼성생명 2025년 지급여력비율 200% 건전성 하락')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_period == fb.metric_period == (2025, None)
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not any(ic._has_explicit_metric_event_assertion(f) for f in (fa, fb))
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)
    assert not ic._should_cluster_features(*[
        replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)
    ])


@pytest.mark.parametrize('right_period', ['2025년', '2025년 6월'])
def test_d_year_only_independent_actions_remain_separate(right_period: str) -> None:
    a = item('삼성생명 2025년 킥스비율 200% 자본확충 완료')
    b = item(f'삼성생명 {right_period} 지급여력비율 200% 후순위채 발행')
    assert ic._metric_match_lacks_event_evidence(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)


def test_d_year_conflict_and_missing_period() -> None:
    a = item('삼성생명 2025년 킥스비율 200% 자본여력 감소')
    b = item('삼성생명 2026년 지급여력비율 201% 건전성 하락')
    assert ic._conflicting_metric_periods(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)
    c = item('삼성생명 지급여력비율 201% 건전성 하락')
    assert not ic._metric_match_lacks_event_evidence(*map(ic._build_cluster_features, (a, c)))
    pair(a, c, False)  # Veto release does not supply the missing ordinary evidence.
