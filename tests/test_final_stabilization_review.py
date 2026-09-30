"""Only nominal enforcement references and terminal headline truncation."""
from __future__ import annotations
from dataclasses import replace
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str, sector: str = '보험', summary: str = '') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': summary, 'url': 'https://example.test/'+title}), [sector], [], [])


def assert_pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


@pytest.mark.parametrize('noun', ['단속', '수사'])
def test_a_nominal_reference_not_enforcement(noun: str) -> None:
    a = item(f'서울시 소상공인 불법사금융 {noun}에 관한 인권보호 조례 전면 개정', '대부')
    b = item('서울시 전통시장 불법대부 특별 수사', '대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None
    assert_pair(a, b, False)


@pytest.mark.parametrize('action', [
    '집중 단속', '특별 단속', '합동 단속', '단속한다', '단속했다', '단속해 적발',
    '수사 개시', '수사 의뢰', '잡는다', '단속에 나선다', '단속에 나섭니다', '수사에 착수',
])
def test_a_actual_actions_preserved(action: str) -> None:
    a = item('서울시 소상공인 미등록대부 '+action, '대부')
    b = item('서울시 전통시장 불법사채 집중 단속', '대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b) == 'enforcement:서울시:small_business'
    assert_pair(a, b, True)


@pytest.mark.parametrize('action', ['수사기관', '단속기관', '바로잡는다', '붙잡는다', '단속에 관한법', '수사에 착수금 지원'])
def test_a_non_actions_remain_out(action: str) -> None:
    a = item('서울시 소상공인 불법사금융 '+action, '대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None


def test_a_separate_real_action_still_counts() -> None:
    a = item('서울시 소상공인 불법사금융 단속에 관한 조례 개정하고 특별 수사 개시', '대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == 'enforcement:서울시:small_business'


def test_a_description_cannot_supply_action() -> None:
    a = item('서울시 소상공인 불법사금융 단속에 관한 조례 개정', '대부', '서울시 특별 단속에 나선다')
    assert ic._issue_fingerprint(a) is None
    assert_pair(a, item('서울시 전통시장 불법대부 특별 수사', '대부'), False)


@pytest.mark.parametrize('ending', ['…', '...', '..', '<b>…</b>', '&hellip;'])
def test_b_terminal_truncation_wire(ending: str) -> None:
    a = item('삼성생명 킥스비율 200% 자본확'+ending)
    b = item('삼성생명 지급여력비율 200% 자본확충')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert ic._metric_event_tokens(fa) == {'자본확'}
    assert ic._metric_event_tokens(fb) == {'자본확충'}
    assert not ic._metric_event_comparison_units(fa) & ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    assert_pair(a, b, True)
    assert fa.norm_title == ic._normalize_title(a.article.title)
    assert fa.tokens == ic._tokenize_title(a.article.title)
    stripped = [replace(f, issue_terms=set(), entities=set(), numbers=set()) for f in (fa, fb)]
    assert not ic._should_cluster_features(*stripped)


@pytest.mark.parametrize('ending', ['.', '!', '?', '… 후순위채 발행', '... 후순위채 발행'])
def test_b_nonterminal_or_other_punctuation_not_truncated(ending: str) -> None:
    a = item('삼성생명 킥스비율 200% 자본확'+ending)
    b = item('삼성생명 지급여력비율 201% 새 회계제도 대응 전략 발표')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert ic._metric_match_lacks_event_evidence(fa, fb)
    assert_pair(a, b, False)


def test_b_truncation_cannot_override_explicit_period_conflict() -> None:
    a = item('삼성생명 1분기 킥스비율 200% 자본확…')
    b = item('삼성생명 2분기 지급여력비율 201% 자본확충')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert ic._conflicting_metric_periods(fa, fb)
    assert_pair(a, b, False)
