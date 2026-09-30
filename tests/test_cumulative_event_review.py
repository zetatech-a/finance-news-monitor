from __future__ import annotations
from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str, sector: str = '보험', summary: str = '') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': summary, 'url': 'https://example.test/'+title}), [sector], [], [])


def features(a, b):
    return ic._build_cluster_features(a), ic._build_cluster_features(b)


@pytest.mark.parametrize('left,right', [
 ('삼성생명 킥스비율 200% 자본확충 완료', '삼성생명 지급여력비율 200% 새 회계제도 대응 전략 발표'),
 ('KB손해보험 KICS 210% 후순위채 발행', 'KB손보 지급여력비율 210% 회계제도 대응 계획'),
 ('삼성생명 등 보험사 KICS 215.2% 자본여력 감소', '보험사 지급여력비율 215.2% 새 회계제도 대응전략 발표'),
 ('삼성생명 2026년 킥스비율 200% 자본확충 완료', '삼성생명 2026년 지급여력비율 200% 새 회계제도 대응 전략 발표'),
])
def test_equal_levels_are_not_event_identity(left, right):
    a,b=item(left),item(right)
    fa,fb=features(a,b)
    assert fa.metric_subjects == fb.metric_subjects
    assert fa.reported_metrics & fb.reported_metrics
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2


@pytest.mark.parametrize('left,right', [
 ('삼성생명 2분기 킥스비율 200% 자본여력 개선','삼성생명 6월말 지급여력비율 200.0% 개선'),
 ('삼성생명 킥스비율 200% 자본여력 개선','삼성생명 지급여력비율 200%로 자본여력 개선'),
])
def test_metric_wires_keep_independent_event_evidence(left,right):
    a,b=item(left),item(right)
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


def test_bare_metric_cannot_bridge_distinct_announcements():
    a=item('삼성생명 킥스비율 200% 자본확충 완료')
    b=item('삼성생명 지급여력비율 200%')
    c=item('삼성생명 지급여력비율 200% 새 회계제도 대응 전략 발표')
    for order in permutations([a,b,c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def campaign(period: str, variant: bool=False):
    return item('서울시 '+period+(' 소상공인 불법대부 특별 수사' if variant else ' 전통시장 불법사금융 집중 단속'), '대부')


@pytest.mark.parametrize('left,right', [('3월','9월'),('2026년 3월','2026년 9월'),('2025년 3월','2026년 3월')])
@pytest.mark.parametrize('prefix',['','단신 '])
def test_campaign_known_dimension_conflicts(left,right,prefix):
    a,b=campaign(left),campaign(right,True)
    a.article.title=prefix+a.article.title;b.article.title=prefix+b.article.title
    fa,fb=features(a,b)
    assert fa.fingerprint == fb.fingerprint
    assert ic._conflicting_enforcement_periods(fa,fb)
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2


@pytest.mark.parametrize('left,right', [('3월','3월'),('2026년 3월',''),('2026년','2026년 3월'),('3월 9월','6월')])
def test_missing_or_ambiguous_month_is_not_conflict(left,right):
    a,b=campaign(left),campaign(right,True)
    assert not ic._conflicting_enforcement_periods(*features(a,b))
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


def test_campaign_month_bridge_all_orders():
    a,b,c=campaign('3월'),campaign(''),campaign('9월',True)
    assert ic._should_cluster(a,b) and ic._should_cluster(b,c)
    for order in permutations([a,b,c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id


def test_description_month_does_not_establish_campaign_period():
    a,b=campaign('3월'),campaign('')
    b.article.description='9월 단속을 배경 사례로 설명'
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


@pytest.mark.parametrize('short,legal,canonical', [('교보생명','교보생명보험','교보생명'),('KB손보','KB손해보험','kb손해보험'),('DB손보','DB손해보험','db손해보험'),('NH농협손보','NH농협손해보험','nh농협손해보험')])
def test_exact_verified_legal_alias_wires(short,legal,canonical):
    a,b=item(short+' 킥스비율 200% 자본여력 감소'),item(legal+' 지급여력비율 200% 자본여력 감소')
    fa,fb=features(a,b)
    assert fa.metric_subjects == fb.metric_subjects == {canonical}
    assert not ic._conflicting_metric_subjects(fa,fb)
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


@pytest.mark.parametrize('left,right',[('교보생명보험','삼성생명'),('교보생명','한화생명'),('교보생명','생보사'),('KB손해보험','손보사'),('가상생명','가상생명보험')])
def test_distinct_or_unverified_subjects(left,right):
    a,b=[item(n+' 킥스비율 200% 자본여력 감소') for n in (left,right)]
    assert ic._conflicting_metric_subjects(*features(a,b))
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2


@pytest.mark.parametrize('prefix,owner', [('삼성생명 등 보험사','보험사'),('삼성생명·한화생명 등 생보사','생보사'),('KB손해보험을 포함한 손보사','손보사'),('삼성생명 포함 보험회사','보험사')])
def test_explicit_aggregate_owns_measurement(prefix,owner):
    a,b=item(prefix+' 킥스비율 215.2% 자본여력 감소'),item(owner+' 지급여력비율 215.2% 자본여력 감소')
    fa,fb=features(a,b)
    assert fa.metric_subjects == fb.metric_subjects == {owner}
    assert not ic._conflicting_metric_subjects(fa,fb)
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


@pytest.mark.parametrize('prefix,expected',[('보험사 중 삼성생명',{'삼성생명'}),('삼성생명',{'삼성생명'}),('삼성생명과 한화생명',{'삼성생명','한화생명'})])
def test_aggregate_is_not_always_the_owner(prefix,expected):
    a=item(prefix+' 킥스비율 215.2%')
    assert ic._build_cluster_features(a).metric_subjects == expected
    if len(expected)>1:
        # Neither listed company may be selected as the sole authoritative owner.
        assert len(ic._build_cluster_features(a).metric_subjects) != 1


@pytest.mark.parametrize('summary,suffix',[('삼성생명 등 보험사 통계',''),('', ' 삼성생명 등 보험사 분석')])
def test_background_or_later_aggregate_cannot_reassign_owner(summary,suffix):
    a=item('삼성생명 킥스비율 215.2%'+suffix,summary=summary)
    b=item('보험사 지급여력비율 215.2%')
    assert ic._build_cluster_features(a).metric_subjects == {'삼성생명'}
    assert ic._conflicting_metric_subjects(*features(a,b))
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2

@pytest.mark.parametrize("ending", ["보험사 킥스 비율 215.2%로 전분기 比 0.8%p↓…손보사 상승·생보사 하...",
                                     "“주가 오르자 위험액도 늘었다”…보험사 지급여력비율 215.2% ‘소폭 하..."])
def test_truncated_statistical_wire_does_not_prove_event_conflict(ending: str) -> None:
    # Stored September 17 titles are cut mid-word: missing overlap is not
    # affirmative evidence of different events. A dated wire supplies the link.
    a = item("보험사 지급여력비율 215.2%로 하락…생·손보 엇갈린 희비")
    b = item("보험사 6월말 킥스비율 215.2% 하락")
    c = item(ending)
    assert not ic._metric_match_lacks_event_evidence(*features(a, c))
    assert len(ic.cluster_tagged_articles([a, b, c])) == 1
