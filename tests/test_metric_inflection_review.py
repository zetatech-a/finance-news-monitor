"""Metric-local morphology must remove false vetoes, never authorize a merge."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(phrase: str, label: str = '킥스비율') -> TaggedArticle:
    title = f'삼성생명 {label} 200% {phrase}'
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/'+title}), ['보험'], [], [])


def assert_wire(left: str, right: str) -> None:
    a, b = item(left), item(right, '지급여력비율')
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert fa.metric_subjects == fb.metric_subjects == {'삼성생명'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_codex_inflected_statistical_wire() -> None:
    a, b = item('자본여력 하락'), item('자본여력은 하락했다', '지급여력비율')
    assert ic._metric_event_tokens(ic._build_cluster_features(a)) == {'자본여력', '하락'}
    assert ic._metric_event_tokens(ic._build_cluster_features(b)) == {'자본여력은', '하락했다'}
    assert_wire('자본여력 하락', '자본여력은 하락했다')


@pytest.mark.parametrize('base,inflected', [
    ('자본여력 점검', '자본여력은 살펴봤다'),
    ('건전성지표 점검', '건전성지표는 살펴봤다'),
    ('자본여력 하락', '자본여력이 줄었다'),
    ('건전성지표 하락', '건전성지표가 줄었다'),
    ('자본여력 점검', '자본여력을 살펴봤다'),
    ('건전성지표 점검', '건전성지표를 살펴봤다'),
    ('자본여력 분석', '자본여력과 위험액 비교'),
    ('건전성지표 분석', '건전성지표와 위험액 비교'),
    ('자본여력 분석', '자본여력의 변화 추적'),
])
def test_complete_noun_particles_do_not_create_conflict(base: str, inflected: str) -> None:
    # Shared noun alone removes a veto; final merge still uses existing rules.
    assert_wire(base, inflected)


@pytest.mark.parametrize('stem,inflected', [
    ('하락', '하락했다'), ('하락', '하락한다'),
    ('상승', '상승했다'), ('상승', '상승한다'),
    ('감소', '감소했다'), ('증가', '증가했다'),
    ('개선', '개선됐다'), ('개선', '개선된다'), ('확대', '확대됐다'),
])
def test_statistical_predicate_inflections(stem: str, inflected: str) -> None:
    assert_wire(stem, inflected)


@pytest.mark.parametrize('left,right', [
    ('자본확충 완료', '새 회계제도 대응 전략 발표'),
    ('후순위채 발행', '회계제도 대응 계획'),
    ('자본여력도', '자본여력'), ('자본여력만', '자본여력'),
    ('국가 개편', '국 업무'), ('사과 개편', '사 업무'), ('하락했다는', '하락'),
    ('회계제도', '회계제'), ('의사 결정', '의 업무'),
])
def test_no_unbounded_suffix_stemming(left: str, right: str) -> None:
    a, b = item(left), item(right, '지급여력비율')
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert ic._metric_match_lacks_event_evidence(fa, fb)
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


def test_morphology_overlap_is_not_a_shortcut_or_global_token_change() -> None:
    a, b = item('자본여력 하락'), item('자본여력은 하락했다', '지급여력비율')
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    # No ordinary issue/entity/number evidence: equal metric/morphology must
    # not become another unconditional True path.
    stripped = [replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)]
    assert not ic._should_cluster_features(*stripped)
    assert '자본여력은' in fb.tokens and '자본여력' not in fb.tokens
    assert ic._tokenize_title(b.article.title) == fb.tokens


def test_bare_metric_bridge_still_cannot_link_disjoint_events() -> None:
    a, bridge, c = item('자본확충 완료'), item('', '지급여력비율'), item('새 회계제도 대응 전략 발표', '지급여력비율')
    for order in permutations([a, bridge, c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id