"""Metric-local event eligibility and signed absolute-level regressions."""
from __future__ import annotations
from dataclasses import replace
from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({'title':title,'summary':'','url':'https://example.test/'+title}),['보험'],[],[])


def features(*titles: str) -> list[ic._ClusterFeatures]:
    return [ic._build_cluster_features(item(t)) for t in titles]


def assert_pair(a: str, b: str, expected: bool) -> None:
    left,right=item(a),item(b)
    assert ic._should_cluster(left,right) is expected
    assert len(ic.cluster_tagged_articles([left,right])) == (1 if expected else 2)


def test_a_different_values_disjoint_events() -> None:
    a='삼성생명 킥스비율 200% 자본확충 완료'
    b='삼성생명 지급여력비율 201% 후순위채 발행'
    fa,fb=features(a,b)
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.reported_metrics == {('capital_adequacy_ratio','200')}
    assert fb.reported_metrics == {('capital_adequacy_ratio','201')}
    assert not fa.reported_metrics & fb.reported_metrics
    assert fa.metric_period == fb.metric_period == (None,None)
    assert ic._metric_event_tokens(fa) == {'자본확충','완료'}
    assert ic._metric_event_tokens(fb) == {'후순위채','발행'}
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert ic._metric_match_lacks_event_evidence(fa,fb)
    assert_pair(a,b,False)


@pytest.mark.parametrize('tail',['자본 확충 완료','자본확충에 나선다','자본확충은 완료'])
def test_a_different_values_same_event(tail: str) -> None:
    a='삼성생명 킥스비율 200% 자본확충 완료'
    b='삼성생명 지급여력비율 201% '+tail
    fa,fb=features(a,b)
    assert not ic._metric_match_lacks_event_evidence(fa,fb)
    assert_pair(a,b,True)
    stripped=[replace(f,issue_terms=set(),entities=set(),numbers=set()) for f in (fa,fb)]
    assert not ic._should_cluster_features(*stripped)


@pytest.mark.parametrize('period,tail',[('', ''),('', '후순위채 발...'),('2분기 ', '후순위채 발행')])
def test_a_missing_truncated_or_dated_evidence_unchanged(period: str, tail: str) -> None:
    fa,fb=features('삼성생명 킥스비율 200% 자본확충 완료',f'삼성생명 {period}지급여력비율 201% {tail}')
    assert not ic._metric_match_lacks_event_evidence(fa,fb)


def test_a_bare_bridge_cannot_join_different_value_events() -> None:
    a=item('삼성생명 킥스비율 200% 자본확충 완료')
    b=item('삼성생명 지급여력비율 201%')
    c=item('삼성생명 지급여력비율 201% 후순위채 발행')
    assert ic._should_cluster(a,b) and ic._should_cluster(b,c)
    for order in permutations([a,b,c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_a_same_value_distinct_event_stays_split() -> None:
    assert_pair('삼성생명 KICS 200% 자본확충 완료','삼성생명 지급여력비율 200% 새 회계제도 대응 전략 발표',False)


def test_b_signed_codex_pair() -> None:
    a='롯데손보 1분기 킥스비율 -5.4% 자본여력 개선'
    b='롯데손보 3월 지급여력비율 5.4% 적기시정조치 우려'
    fa,fb=features(a,b)
    assert fa.metric_subjects == fb.metric_subjects == {'롯데손보'}
    assert fa.metric_period == fb.metric_period == (None,3)
    assert fa.reported_metrics == {('capital_adequacy_ratio','-5.4')}
    assert fb.reported_metrics == {('capital_adequacy_ratio','5.4')}
    assert_pair(a,b,False)


@pytest.mark.parametrize('label',['K-ICS','K ICS','KICS','킥스','킥스비율','킥스 비율','지급여력비율','지급여력 비율'])
@pytest.mark.parametrize('value,expected',[('-5.40','-5.4'),('-5.400','-5.4'),('+5.4','5.4')])
def test_b_signed_label_facts(label: str,value: str,expected: str) -> None:
    title=f'삼성생명 2분기 {label} {value}% 자본여력 개선'
    f=features(title)[0]
    assert f.reported_metrics == {('capital_adequacy_ratio',expected)}
    assert f.metric_identities == {'capital_adequacy_ratio'}
    assert f.metric_subjects == {'삼성생명'}
    assert f.metric_period == (None,6)
    assert f.norm_title == ic._normalize_title(title)
    assert f.tokens == ic._tokenize_title(title)
    assert f.numbers == ic._extract_numbers(title)


@pytest.mark.parametrize('suffix',['p',' p','포인트',' 포인트'])
@pytest.mark.parametrize('sign',['','-'])
def test_b_percentage_points_are_not_levels(suffix: str,sign: str) -> None:
    a=f'삼성생명 1분기 K-ICS {sign}5.4%{suffix} 하락'
    b=f'삼성생명 2분기 지급여력비율 {sign}5.4%{suffix} 하락'
    fa,fb=features(a,b)
    assert not fa.reported_metrics and not fb.reported_metrics
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert ic._conflicting_metric_periods(fa,fb)
    assert_pair(a,b,False)
    assert_pair(a,a.replace('삼성생명','한화생명'),False)


def test_b_equal_signed_values_keep_dated_shortcut() -> None:
    a='삼성생명 2분기 K-ICS -5.40% 자본여력 개선'
    b='삼성생명 6월말 지급여력비율 -5.400% 개선'
    fa,fb=features(a,b)
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio','-5.4')}
    assert_pair(a,b,True)
    assert_pair(a.replace('-5.40','+5.4'),b.replace('-5.400','5.40'),True)


def test_ab_opposite_sign_disjoint_undated_events() -> None:
    a='삼성생명 KICS -5.4% 자본확충 완료'
    b='삼성생명 지급여력비율 5.4% 후순위채 발행'
    fa,fb=features(a,b)
    assert not fa.reported_metrics & fb.reported_metrics
    assert ic._metric_match_lacks_event_evidence(fa,fb)
    assert_pair(a,b,False)


def test_b_non_metric_and_unrelated_minus() -> None:
    f=features('삼성생명 킥스타터 -5.4% 프로젝트')[0]
    assert not f.reported_metrics and not f.metric_identities
    assert features('삼성생명 -5.4% 손실 KICS 200% 자본확충')[0].reported_metrics == {('capital_adequacy_ratio','200')}


def test_b_stored_negative_headline() -> None:
    title='롯데손보, 2분기 흑자전환…기본자본 K-ICS도 -5.4%로 개선'
    assert features(title)[0].reported_metrics == {('capital_adequacy_ratio','-5.4')}


def test_b_raw_markup_and_numeric_formatting() -> None:
    title='삼성생명 2분기 <b>K-ICS</b> -05.400% 자본여력 개선'
    assert features(title)[0].reported_metrics == {('capital_adequacy_ratio','-5.4')}
