"""Regressions for the three Codex findings on b81261c (PR #85)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.evaluate_loan_news import make_article, replay
from src.pipeline.issue_cluster import (
    _build_cluster_features, _metric_subjects, _normalize_title, _reported_metrics,
    _should_cluster, _should_cluster_features, cluster_tagged_articles,
)
from src.pipeline.relevance_filter import has_domain_anchor
from src.pipeline.relevance_score import matched_terms
from src.pipeline.tagger import TaggedArticle
from src.pipeline.text_matcher import contains_term


def tagged(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': '',
                                      'url': 'https://example.test/' + title}), ['보험'], [], [])


@pytest.mark.parametrize('suffix', ['%포인트', '% 포인트', '%p', '% p'])
def test_percentage_point_changes_are_not_metric_levels(suffix: str) -> None:
    assert not _reported_metrics(_normalize_title(f'KB손해보험 지급여력비율 20{suffix} 하락'))


@pytest.mark.parametrize('metric,value', [
    ('지급여력비율', '20'), ('K-ICS 비율', '215.2'), ('킥스 비율', '215.2'),
])
def test_actual_metric_levels_remain_recognized(metric: str, value: str) -> None:
    assert _reported_metrics(_normalize_title(f'KB손해보험 {metric} {value}%')) == {
        ('capital_adequacy_ratio', value)}


def test_level_and_change_in_same_headline() -> None:
    assert _reported_metrics(_normalize_title(
        'KB손해보험 K-ICS 비율 215.2%, 지급여력비율 20% 포인트 하락')) == {
            ('capital_adequacy_ratio', '215.2')}


def test_percentage_point_change_does_not_enable_metric_shortcut() -> None:
    a = _build_cluster_features(tagged('KB손보 지급여력비율 20%포인트 하락'))
    b = _build_cluster_features(tagged('KB손해보험 킥스 비율 20% 경영개선 권고'))
    # Establish that this fixture has no independent similarity/fingerprint path.
    assert not _should_cluster_features(replace(a, reported_metrics=set()), b)
    assert not _should_cluster_features(a, b)


def test_aggregate_insurer_synonyms_are_same_subject_and_event() -> None:
    a = tagged('보험사 킥스비율 215.2% 자본여력 감소')
    b = tagged('보험회사 킥스비율 215.2% 자본여력 감소')
    assert _metric_subjects(_normalize_title(a.article.title)) == _metric_subjects(_normalize_title(b.article.title))
    assert _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 1


@pytest.mark.parametrize('left,right', [
    ('보험사', '생보사'), ('보험회사', '손보사'), ('생보사', '손보사'),
    ('은행권', '저축은행권'), ('KB손해보험', '보험회사'),
    ('삼성생명', '한화생명'), ('KB손해보험', 'DB손해보험'),
])
def test_distinct_metric_subject_scopes_stay_separate(left: str, right: str) -> None:
    a = tagged(f'{left} 킥스비율 215.2% 자본여력 감소')
    b = tagged(f'{right} 킥스비율 215.2% 자본여력 감소')
    assert not _should_cluster(a, b)


@pytest.mark.parametrize('text,term', [
    ('불법 대부도 토지거래', '불법대부'), ('미등록 대부도 숙박업체', '미등록대부'),
])
def test_spaced_loan_aliases_do_not_revive_island_noise(text: str, term: str) -> None:
    assert not contains_term(text, term)
    article = make_article({'title': text, 'summary': '', 'url': 'https://example.test/noise'})
    assert term not in matched_terms(article)['hard']
    assert not has_domain_anchor(article)
    # Even a high candidate-model probability must not bypass the anchor guard.
    assert not replay([{'title': text, 'summary': '', 'url': article.link, 'prob': .8}])[0]


@pytest.mark.parametrize('alias,term', [('불법 대부', '불법대부'), ('미등록 대부', '미등록대부')])
@pytest.mark.parametrize('continuation', ['도', 'abc', '123'])
def test_spaced_loan_alias_right_boundary(alias: str, term: str, continuation: str) -> None:
    assert not contains_term(alias + continuation, term)


@pytest.mark.parametrize('text,term', [
    ('불법 대부 피해', '불법대부'), ('불법 대부 단속', '불법대부'),
    ('미등록 대부업자 적발', '대부업'), ('미등록 대부 영업', '미등록대부'),
    ('불법대부 피해', '불법대부'), ('미등록대부 적발', '미등록대부'),
    ('불법 대부', '불법대부'), ('미등록 대부, 적발', '미등록대부'),
])
def test_financial_loan_aliases_keep_positive_recall(text: str, term: str) -> None:
    assert contains_term(text, term)
    row = {'title': text, 'summary': '', 'url': 'https://example.test/finance', 'prob': .8}
    article = make_article(row)
    assert term in matched_terms(article)['hard']
    assert has_domain_anchor(article)
    assert len(replay([row])[0]) == 1


@pytest.mark.parametrize('text', [
    '공유재산 대부', '공유재산 대부계약', '대부료', '토지 대부', '공공시설 대부', '대부도',
])
def test_nonfinancial_loan_homonyms_stay_irrelevant(text: str) -> None:
    row = {'title': text, 'summary': '', 'url': 'https://example.test/noise', 'prob': .8}
    assert not has_domain_anchor(make_article(row))
    assert not replay([row])[0]


@pytest.mark.parametrize('prefix,term', [('불법', '불법대부'), ('미등록', '미등록대부')])
@pytest.mark.parametrize('compound', ['대부업', '대부업체', '대부업권', '대부업계', '대부업자', '대부중개업'])
def test_explicit_financial_compounds_keep_gray_zone_recall(prefix: str, term: str, compound: str) -> None:
    # A plain 대부업 anchor scores only 5, below the existing gray-zone floor of 6.
    # Recognize the explicit illegal/unregistered compound, not a loose 대부 prefix.
    row = {'title': f'{prefix} {compound} 적발', 'summary': '',
           'url': 'https://example.test/compound', 'prob': .5}
    assert term in matched_terms(make_article(row))['hard']
    assert len(replay([row])[0]) == 1
