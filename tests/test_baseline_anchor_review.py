"""Comparison baselines and repository-supported enforcement domain parity."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations
from pathlib import Path
import pytest
import yaml
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic, relevance_filter as rf, relevance_score as rs
from src.pipeline.tagger import TaggedArticle, tag_articles


def item(title: str, sector: str = '보험', summary: str = '') -> TaggedArticle:
    return TaggedArticle(make_article({'title':title,'summary':summary,'url':'https://example.test/'+title}),[sector],[],[])


def assert_pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a,b) is merge
    assert len(ic.cluster_tagged_articles([a,b])) == (1 if merge else 2)


@pytest.mark.parametrize('prefix', ['전년 3월 대비','3월 대비','3월보다','2025년 3월 대비','1분기 대비','상반기보다','3월말 대비','3월 말보다'])
def test_a_baseline_is_not_snapshot(prefix: str) -> None:
    a=item(f'삼성생명 {prefix} 킥스비율 200% 자본여력 개선')
    b=item('삼성생명 2026년 6월 기준 지급여력비율 201% 자본여력 개선')
    fa,fb=[ic._build_cluster_features(t) for t in (a,b)]
    assert fa.metric_period == (None,None)
    assert not ic._conflicting_metric_periods(fa,fb)
    assert_pair(a,b,True)


@pytest.mark.parametrize('prefix,expected', [
    ('3월 대비 6월 기준',(None,6)),
    ('2025년 대비 2026년 6월 기준',(2026,6)),
    ('2025년 3월 대비 2026년 6월 기준',(2026,6)),
    ('2026년 6월 기준 3월 대비',(2026,6)),
])
def test_a_baseline_does_not_erase_current_period(prefix: str, expected: tuple[int | None,int | None]) -> None:
    a=item(f'삼성생명 {prefix} KICS 200% 자본여력 개선')
    b=item('삼성생명 2026년 2분기 지급여력비율 200% 자본여력 개선')
    assert ic._build_cluster_features(a).metric_period == expected
    assert_pair(a,b,True)
    assert_pair(a,item('삼성생명 2026년 3월 기준 KICS 201% 자본여력 개선'),False)


@pytest.mark.parametrize('prefix,expected', [
    ('3월 기준',(None,3)),('3월말',(None,3)),('3월 말',(None,3)),
    ('1분기',(None,3)),('2분기',(None,6)),('상반기',(None,6)),
    ('3분기',(None,9)),('4분기',(None,12)),('3월 6월',(None,None)),
    ('3월 대비책',(None,3)),('3월물 대비',(None,None)),
    ('3월 기준 보험사 대비',(None,3)),
])
def test_a_existing_snapshots_and_bounded_marker(prefix: str, expected: tuple[int | None,int | None]) -> None:
    assert ic._metric_period(ic._normalize_title(f'삼성생명 {prefix} KICS 200%')) == expected


def test_a_no_global_or_enforcement_date_removal() -> None:
    title='삼성생명 KICS 200% 3월 대비 개선'
    assert ic._metric_period(ic._normalize_title(title)) == (None,None)
    assert '3월' in ic._tokenize_title(title)
    assert ic._enforcement_period('서울시 3월 전통시장 불법대부 집중 단속') == (None,3)
    a=item('삼성생명 3월 대비 KICS 200% 자본확충 완료')
    b=item('삼성생명 지급여력비율 200% 새 회계제도 대응 전략 발표')
    assert_pair(a,b,False)


def supported_wire(domain: str) -> tuple[TaggedArticle,TaggedArticle]:
    titles=[f'서울시 소상공인 {domain} 집중 단속','서울시 소상공인 불법대부 특별 수사']
    articles=[make_article({'title':t,'summary':'','url':'https://example.test/'+t}) for t in titles]
    for article in articles:
        terms=rs.matched_terms(article)
        assert rs.relevance_score(article)>=6
        assert rf.has_domain_anchor(article)
        keep,_=rf._decide_relevance(score=rs.relevance_score(article),prob=.8,min_score=4,min_prob=.55,
            model_policy='candidate_hybrid',article_text=article.title,
            matched_hard='|'.join(terms['hard']),matched_negative='|'.join(terms['negative']))
        assert keep
    taxonomy=yaml.safe_load(Path('queries.yml').read_text(encoding='utf-8'))
    tagged=tag_articles(articles,taxonomy['sectors'],taxonomy['topics'])
    assert all(t.sectors[0]=='대부' for t in tagged)
    return tagged[0],tagged[1]


@pytest.mark.parametrize('domain',['미등록대부','미등록 대부','불법사채','불법 사채'])
def test_b_existing_domain_reaches_clustering_and_guard_is_cause(domain: str) -> None:
    a,b=supported_wire(domain)
    fa,fb=[ic._build_cluster_features(t) for t in (a,b)]
    # Ordinary evidence already suffices without the asymmetric enforcement guard.
    assert ic._should_cluster_features(replace(fa,fingerprint=None),replace(fb,fingerprint=None))
    assert ic._is_enforcement_headline(a.article.title)
    assert fa.fingerprint==fb.fingerprint=='enforcement:서울시:small_business'
    assert_pair(a,b,True)


@pytest.mark.parametrize('domain',['미등록대부','불법사채'])
@pytest.mark.parametrize('agency',['수사기관','단속기관'])
def test_b_domain_alone_never_supplies_action(domain: str, agency: str) -> None:
    a=item(f'서울시 소상공인 {domain} {agency} 인권교육 지원 조례 개정','대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None
    assert_pair(a,item('서울시 소상공인 불법대부 집중 단속','대부'),False)


@pytest.mark.parametrize('action',['집중단속','특별단속','합동단속','단속에 나선다','단속이 시작','단속을 실시','수사의 결과','단속해 적발','단속한다','수사개시','수사의뢰','잡는다'])
def test_b_established_action_grammar(action: str) -> None:
    a=item('서울시 소상공인 미등록대부 '+action,'대부')
    b=item('서울시 소상공인 불법대부 집중 단속','대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert_pair(a,b,True)


@pytest.mark.parametrize('domain',['미등록대부','불법사채'])
def test_b_campaign_conflict_and_bridge(domain: str) -> None:
    a=item(f'서울시 3월 소상공인 {domain} 집중 단속','대부')
    bridge=item('서울시 소상공인 불법대부 특별 수사','대부')
    c=item('서울시 9월 소상공인 불법대부 집중 단속','대부')
    assert_pair(a,bridge,True)
    for order in permutations([a,bridge,c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id!=c.article.cluster_id


@pytest.mark.parametrize('title',[
    '서울시 소상공인 미등록 대부도 수사기관 교육',
    '서울시 소상공인 미등록 대부도 집중 단속',
    '서울시 소상공인 미등록 대부abc 집중 단속',
    '서울시 소상공인 불법사채 바로잡는다',
    '서울시 소상공인 미등록대부 붙잡는다',
])
def test_b_unsupported_alias_or_verb_remains_out(title: str) -> None:
    assert not ic._is_enforcement_headline(title)


def test_b_description_cannot_supply_event() -> None:
    a=item('서울시 소상공인 지원 정책 발표','대부','서울시 미등록대부 집중 단속 사례를 배경으로 설명')
    assert ic._issue_fingerprint(a) is None
    assert_pair(a,item('서울시 소상공인 불법대부 집중 단속','대부'),False)
