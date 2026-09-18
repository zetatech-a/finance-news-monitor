"""Regressions for Codex's four correctness findings on 676a722."""
from __future__ import annotations

from dataclasses import replace
import pytest

from scripts.evaluate_loan_news import FIXTURE, load_rows, make_article, replay
from src.pipeline import issue_cluster as ic, relevance_filter as rf, relevance_score as rs
from src.pipeline.tagger import TaggedArticle


def tagged(title: str, sector: str = '보험') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/' + title}), [sector], [], [])


def test_multiple_companies_do_not_cross_assign_metric_values() -> None:
    a = tagged('삼성생명 킥스비율 200%, 한화생명 킥스비율 180%')
    b = tagged('삼성생명 지급여력비율 180% 자본확충 완료')
    # No independent similarity path: isolates the erroneous metric shortcut.
    assert not ic._should_cluster_features(replace(ic._build_cluster_features(a), reported_metrics=set()), ic._build_cluster_features(b))
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize('title', [
    '삼성생명 킥스비율 200%, 지급여력비율 180%',
    '삼성생명 킥스비율 200%, 한화생명 킥스비율 180%',
    '삼성생명 킥스비율 200%, 한화생명 킥스비율 200.0%',
])
def test_multiple_measurements_have_no_authoritative_metric_subject(title: str) -> None:
    assert not ic._metric_subjects(ic._normalize_title(title))


@pytest.mark.parametrize('subject', ['삼성생명', '보험사'])
def test_single_subject_measurement_shortcut_survives(subject: str) -> None:
    a = tagged(f'{subject} 킥스비율 200%')
    b = tagged(f'{subject} 지급여력비율 200% 자본확충 완료')
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_missing_subject_does_not_authorize_metric_shortcut() -> None:
    assert not ic._metric_subjects('킥스비율 200%')
    assert not ic._should_cluster(tagged('킥스비율 200% 요구자본 확충 성공'),
                                  tagged('지급여력비율 200% 새 회계제도 대응 여력 입증'))


@pytest.mark.parametrize('verb', ['바로잡는다', '붙잡는다', '다잡는다', '잡는다며'])
def test_compound_verb_is_not_standalone_enforcement(verb: str) -> None:
    policy = tagged(f'서울시 소상공인 불법사금융 채무조정 신청 절차 허점 {verb}', '대부')
    enforcement = tagged('서울시 소상공인 불법사금융 집중 단속', '대부')
    assert not ic._is_enforcement_headline(policy.article.title)
    assert ic._issue_fingerprint(policy) != ic._issue_fingerprint(enforcement)
    assert len(ic.cluster_tagged_articles([policy, enforcement])) == 2


@pytest.mark.parametrize('ending', ['잡는다', '“잡는다”', '잡는다!', '불법사금융을 잡는다'])
def test_standalone_enforcement_verb_preserves_fingerprint(ending: str) -> None:
    article = tagged(f'서울시 소상공인 불법사금융 {ending}', '대부')
    assert ic._is_enforcement_headline(article.article.title)
    assert ic._issue_fingerprint(article) == 'enforcement:서울시:small_business'


def decision(title: str) -> tuple:
    article = make_article({'title': title, 'summary': '', 'url': 'https://example.test/relevance'})
    terms = rs.matched_terms(article)
    score = rs.relevance_score(article)
    keep, reason = rf._decide_relevance(
        score=score, prob=.8, min_score=4, min_prob=.55, model_policy='candidate_hybrid',
        article_text=title, matched_hard='|'.join(terms['hard']), matched_negative='|'.join(terms['negative']))
    return terms, rf._has_strong_finance_context(title), score, rf.has_domain_anchor(article), keep, reason


@pytest.mark.parametrize('canonical,alias,score', [
    ('불법사금융', '불법 사금융', 4), ('불법대부', '불법 대부', 2),
    ('미등록대부', '미등록 대부', 2), ('불법사채', '불법 사채', 2),
    ('불법추심', '불법 추심', 2),
])
@pytest.mark.parametrize('noise', ['여행', '맛집'])
def test_alias_context_parity_retains_canonical_noise_policy(canonical: str, alias: str, score: int, noise: str) -> None:
    compact = decision(f'{canonical} {noise}')
    spaced = decision(f'{alias} {noise}')
    assert compact[1:5] == (False, score, True, False)
    assert compact == spaced


@pytest.mark.parametrize('canonical,alias', [
    ('불법사금융', '불법 사금융'), ('불법대부', '불법 대부'),
    ('미등록대부', '미등록 대부'), ('불법사채', '불법 사채'), ('불법추심', '불법 추심'),
])
def test_independent_risk_signal_keeps_alias_context_parity(canonical: str, alias: str) -> None:
    compact, spaced = decision(f'{canonical} 피해 확산 여행'), decision(f'{alias} 피해 확산 여행')
    assert compact == spaced
    assert compact[1] and compact[4]


@pytest.mark.parametrize('left,right,value', [('200', '200.0', '200'), ('200', '200.00', '200'), ('215.2', '215.20', '215.2')])
def test_numeric_metric_canonicalization_cannot_bypass_period_veto(left: str, right: str, value: str) -> None:
    a = tagged(f'KB손해보험 1분기 킥스비율 {left}% 자본확충 완료')
    b = tagged(f'KB손해보험 2분기 킥스비율 {right}% 자본확충 완료')
    assert len(ic.cluster_tagged_articles([a, b])) == 2
    assert ic._reported_metrics(ic._normalize_title(b.article.title)) == {('capital_adequacy_ratio', value)}


@pytest.mark.parametrize('left,right', [('200', '200.0'), ('200', '200.00'), ('215.2', '215.20')])
def test_same_period_numeric_spellings_retain_wire_shortcut(left: str, right: str) -> None:
    a = tagged(f'KB손해보험 2분기 킥스비율 {left}% 자본확충 완료')
    b = tagged(f'KB손보 6월말 K-ICS 비율 {right}% 후순위채 발행')
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_multiple_measurements_retain_ordinary_wire_similarity() -> None:
    a = tagged('삼성생명 킥스비율 200%, 지급여력비율 180%')
    b = tagged('삼성생명 킥스비율 200%, 지급여력비율 180% 발표')
    assert not ic._metric_subjects(ic._normalize_title(a.article.title))
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_multiple_subjects_single_measurement_is_not_single_company_fact() -> None:
    assert len(ic._metric_subjects('삼성생명 한화생명 킥스비율 200%')) == 2
    assert not ic._should_cluster(tagged('삼성생명 한화생명 킥스비율 200% 자본정책 비교'),
                                  tagged('삼성생명 지급여력비율 200% 후순위채 발행'))


def test_alias_context_preserves_independent_illegality_signal() -> None:
    compact = decision('불법사금융 불법 영업 여행')
    assert compact == decision('불법 사금융 불법 영업 여행')
    assert compact[1] and compact[4]


@pytest.mark.parametrize('text', ['불법 대부도 여행', '미등록 대부도 여행', '불법 대부abc', '미등록 대부123'])
def test_context_guard_does_not_turn_unsupported_alias_into_anchor(text: str) -> None:
    assert rs._lending_context_text(text) == text.lower()
    evidence = decision(text)
    assert not evidence[3] and not evidence[4]



def test_golden_court_article_retains_keep_without_spacing_bonus() -> None:
    row = next(row for row in load_rows(FIXTURE)
               if row['url'] == 'https://www.ytn.co.kr/_ln/0103_202609161700525967')
    compact = dict(row, summary=row['summary'].replace('불법 사채', '불법사채'))
    assert compact['summary'] != row['summary']
    assert rs.relevance_score(make_article(row)) == rs.relevance_score(make_article(compact)) == 6
    assert rs.matched_terms(make_article(row)) == rs.matched_terms(make_article(compact))
    assert rf.has_domain_anchor(make_article(row))
    assert len(replay([row])[0]) == len(replay([compact])[0]) == 1
