"""Bare enforcement nouns and dated independent action assertions."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations

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


@pytest.mark.parametrize('phrase', [
    '단속 관련 인권보호 조례 전면 개정', '수사 관련 제도 개선',
    '단속 절차를 규정한 조례', '수사 제도 관련 정책 발표',
    '수사기관', '단속기관', '바로잡는다', '붙잡는다',
    '단속에 관한 조례', '수사에 관한 규정', '단속의 절차', '단속은 조례 적용 대상',
])
def test_nominal_enforcement_is_not_an_action(phrase: str) -> None:
    a = item('서울시 소상공인 불법사금융 ' + phrase, '대부')
    b = item('서울시 전통시장 불법대부 특별 수사 착수', '대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None
    pair(a, b, False)


@pytest.mark.parametrize('action', [
    '집중단속', '집중 단속', '특별단속', '특별 단속', '합동단속', '합동 단속',
    '보완수사', '보완 수사', '인지수사', '인지 수사',
    '단속에 나선다', '단속에 나섭니다', '수사에 착수', '단속을 실시',
    '단속이 시작', '단속이 시작됐다', '수사의 결과', '단속해', '단속한다',
    '단속했다', '수사개시', '수사 개시', '수사의뢰', '수사 의뢰', '잡는다',
    '집중단속에 나선다',
])
def test_established_actions_keep_fingerprint_and_wire(action: str) -> None:
    a = item('서울시 소상공인 미등록대부 ' + action, '대부')
    b = item('서울시 전통시장 불법사채 특별 수사', '대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b) == 'enforcement:서울시:small_business'
    pair(a, b, True)


def test_nominal_reference_can_coexist_with_independent_title_action() -> None:
    a = item('서울시 소상공인 불법사금융 단속 절차 개정 및 특별 수사 개시', '대부')
    assert ic._issue_fingerprint(a) == 'enforcement:서울시:small_business'


def test_description_does_not_supply_action() -> None:
    a = item('서울시 소상공인 불법사금융 단속 절차 개정', '대부', '특별 수사 착수')
    assert ic._issue_fingerprint(a) is None
    pair(a, item('서울시 전통시장 불법대부 특별 수사', '대부'), False)


@pytest.mark.parametrize('left,right', [
    ('대규모 자사주 매입 결정', '후순위채 조기 상환'),
    ('배당안 의결', '후순위채 조기 상환'),
    ('자사주 매입', '후순위채 조기 상환'),
])
def test_dated_independent_actions_veto_equal_fact_shortcut(left: str, right: str) -> None:
    a = item('삼성생명 2분기 킥스비율 200% ' + left)
    b = item('삼성생명 상반기 지급여력비율 200% ' + right)
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert fa.metric_period == fb.metric_period == (None, 6)
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, False)


@pytest.mark.parametrize('context', [
    '상환 부담 증가', '매입 규모 증가', '상환액 증가', '매입액 증가',
    '결정적 영향', '의결권 비중 증가', '소폭 하락', '요구자본 증가',
])
def test_context_is_not_independent_dated_assertion(context: str) -> None:
    a = item('삼성생명 2분기 킥스비율 200% ' + context)
    b = item('삼성생명 상반기 지급여력비율 200% 새 전략 발표')
    assert not ic._metric_match_lacks_event_evidence(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, True)


@pytest.mark.parametrize('action', ['완료', '발행', '발표', '계획', '성공', '입증'])
def test_existing_dated_assertions_remain(action: str) -> None:
    a = item('삼성생명 2분기 킥스비율 200% 자본확충 ' + action)
    b = item('삼성생명 상반기 지급여력비율 200% 후순위채 조기 상환')
    assert ic._metric_match_lacks_event_evidence(*map(ic._build_cluster_features, (a, b)))
    pair(a, b, False)


def test_same_event_spacing_is_not_new_merge_evidence() -> None:
    a = item('삼성생명 2분기 킥스비율 200% 자본확충 결정')
    b = item('삼성생명 상반기 지급여력비율 201% 자본 확충에 나선다')
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)
    assert not ic._should_cluster_features(*[
        replace(f, entities=set(), issue_terms=set(), numbers=set()) for f in (fa, fb)
    ])


def test_bare_dated_bridge_cannot_join_independent_actions() -> None:
    a = item('삼성생명 2분기 킥스비율 200% 대규모 자사주 매입 결정')
    b = item('삼성생명 상반기 지급여력비율 200%')
    c = item('삼성생명 상반기 지급여력비율 200% 후순위채 조기 상환')
    for order in permutations([a, b, c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id
