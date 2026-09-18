"""Correctness regressions for the four Codex findings on 3b6e598."""
from __future__ import annotations

from itertools import permutations
import json
from pathlib import Path
import sys

import pytest

from scripts import evaluate_cluster_review as evaluation
from scripts.evaluate_loan_news import make_article, replay
from src.pipeline.issue_cluster import _metric_subjects, _normalize_title, _should_cluster, cluster_tagged_articles
from src.pipeline.relevance_filter import has_domain_anchor
from src.pipeline.relevance_score import matched_terms
from src.pipeline.tagger import TaggedArticle
from src.pipeline.text_matcher import contains_term


def tagged(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/' + title}), ['보험'], [], [])


@pytest.mark.parametrize('left,right', [
    ('1분기', '2분기'), ('2025년 2분기', '2026년 2분기'), ('2025년', '2026년'),
    ('상반기', '하반기'), ('3월말', '6월 말'),
])
def test_explicit_metric_period_conflict_prevents_final_merge(left: str, right: str) -> None:
    a = tagged(f'KB손보 {left} 킥스비율 200% 자본확충 완료')
    b = tagged(f'KB손해보험 {right} 지급여력비율 200% 후순위채 발행')
    assert not _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize('left,right', [
    ('2분기', '2분기'), ('2분기', ''), ('2026년 2분기', '2분기'),
    ('2분기', '상반기'), ('상반기', '6월 말'), ('2분기말', '6월말'),
])
def test_same_or_missing_metric_period_retains_wire_recall(left: str, right: str) -> None:
    a = tagged(f'KB손보 {left} 킥스비율 200% 자본확충 완료')
    b = tagged(f'KB손해보험 {right} 지급여력비율 200% 후순위채 발행')
    assert _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 1


def test_conflicting_periods_cannot_bridge_through_missing_period() -> None:
    a = tagged('KB손보 1분기 킥스비율 200% 자본확충 완료')
    b = tagged('KB손해보험 킥스비율 200% 자본확충 완료')
    c = tagged('KB손해보험 2분기 킥스비율 200% 자본확충 완료')
    assert _should_cluster(a, b) and _should_cluster(b, c)
    for order in permutations([a, b, c]):
        cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_period_veto_also_precedes_low_value_similarity() -> None:
    assert len(cluster_tagged_articles([
        tagged('단신 KB손해보험 1분기 킥스비율 200% 달성'),
        tagged('단신 KB손해보험 2분기 킥스비율 200% 달성'),
    ])) == 2


@pytest.mark.parametrize('text,term', [
    ('불법 대부를 적발', '불법대부'), ('미등록 대부가 기승', '미등록대부'),
    ('불법 대부는 처벌 대상', '불법대부'), ('미등록 대부의 피해', '미등록대부'),
])
def test_spaced_loan_particles_preserve_real_relevance(text: str, term: str) -> None:
    row = {'title': text, 'summary': '', 'url': 'https://example.test/particle', 'prob': .8}
    article = make_article(row)
    assert contains_term(text, term)
    assert term in matched_terms(article)['hard']
    assert has_domain_anchor(article)
    assert len(replay([row])[0]) == 1


@pytest.mark.parametrize('ending', ['도 토지거래', 'abc', '123', '가능한', '가입', '에서부터'])
@pytest.mark.parametrize('prefix,term', [('불법', '불법대부'), ('미등록', '미등록대부')])
def test_lexical_continuations_are_not_particles(prefix: str, term: str, ending: str) -> None:
    assert not contains_term(f'{prefix} 대부{ending}', term)


@pytest.mark.parametrize('left,right', [('생명보험', '생보사'), ('손해보험', '손보사'), ('보험회사', '보험사')])
def test_metric_industry_synonyms_from_both_extraction_paths(left: str, right: str) -> None:
    a = tagged(f'{left} 킥스비율 215.2% 자본여력 감소')
    b = tagged(f'{right} 킥스비율 215.2% 자본여력 감소')
    assert _metric_subjects(_normalize_title(a.article.title)) == _metric_subjects(_normalize_title(b.article.title))
    assert len(cluster_tagged_articles([a, b])) == 1


@pytest.mark.parametrize('left,right', [
    ('보험사', '생보사'), ('보험사', '손보사'), ('생보사', '손보사'),
    ('은행권', '저축은행권'), ('삼성생명', '한화생명'), ('KB손해보험', 'DB손해보험'),
    ('KB손해보험', '손보사'), ('삼성생명', '생보사'), ('교보생명', '생명보험'),
])
def test_named_companies_and_different_industries_stay_distinct(left: str, right: str) -> None:
    assert not _should_cluster(tagged(f'{left} 킥스비율 215.2% 자본여력 감소'),
                               tagged(f'{right} 킥스비율 215.2% 자본여력 감소'))


def test_cli_base_compared_to_itself_is_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise actual main() preparation dispatch without retaining large CSVs.
    original_load = evaluation.replay.load_rows
    fixture = original_load(evaluation.replay.FIXTURE)
    monkeypatch.setattr(evaluation.replay, 'load_rows',
                        lambda path: fixture if path.suffix == '.csv' else original_load(path))
    output = tmp_path / 'comparison.json'
    monkeypatch.setattr(sys, 'argv', ['replay', '--output', str(output), '--compare-revision', evaluation.BASE])
    evaluation.main()
    result = json.loads(output.read_text(encoding='utf-8'))
    for day in result['days'].values():
        assert day['base'] == day['comparison']
    assert result['golden_fixed_relevant_cohort']['base'] == result['golden_fixed_relevant_cohort']['comparison']


@pytest.mark.parametrize('suffix', ['을', '를', '이', '가', '은', '는', '의', '에', '에서', '와', '과', '로', '으로'])
def test_complete_particle_suffix_requires_its_own_boundary(suffix: str) -> None:
    assert contains_term(f'미등록 대부{suffix} 피해', '미등록대부')
    assert not contains_term(f'미등록 대부{suffix}abc', '미등록대부')
    assert not contains_term(f'미등록 대부{suffix}가능', '미등록대부')


def test_background_comparison_after_metric_does_not_set_period() -> None:
    a = tagged('KB손보 2분기 킥스비율 200% 1분기 대비 개선')
    b = tagged('KB손해보험 2분기 지급여력비율 200% 후순위채 발행')
    assert _should_cluster(a, b)


def test_ambiguous_period_does_not_invent_conflict() -> None:
    assert _should_cluster(tagged('KB손보 1분기 2분기 킥스비율 200% 유지'),
                           tagged('KB손해보험 2분기 지급여력비율 200% 후순위채 발행'))
