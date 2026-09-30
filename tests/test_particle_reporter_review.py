"""Bound particle-bearing enforcement evidence and exclude the metric reporter."""
from __future__ import annotations

from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str, sector: str = '보험', summary: str = '') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': summary, 'url': 'https://example.test/'+title}), [sector], [], [])


def pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


@pytest.mark.parametrize('word', ['단속의', '단속은', '단속이', '단속을', '수사의', '수사는', '수사가', '수사를'])
def test_a_particle_alone_is_not_action(word: str) -> None:
    endings = {'의': '절차를 정한 조례 개정', '은': '인권보호 조례의 적용 대상',
               '는': '인권보호 조례의 적용 대상', '이': '인권보호 조례의 적용 대상',
               '가': '인권보호 조례의 적용 대상', '을': '대상으로 한 인권보호 조례 개정',
               '를': '대상으로 한 인권보호 조례 개정'}
    a = item('서울시 소상공인 불법사금융 '+word+' '+endings[word[-1]], '대부')
    b = item('서울시 소상공인 불법사금융 집중 단속', '대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None
    pair(a, b, False)


@pytest.mark.parametrize('action', [
    '단속을 실시', '단속이 시작', '단속이 시작됐다', '수사의 결과',
    '집중 단속', '특별단속', '합동단속', '단속한다', '단속했다', '단속해 적발',
    '수사 개시', '수사 의뢰', '단속에 나선다', '단속에 나섭니다', '수사에 착수', '잡는다',
])
def test_a_existing_action_or_result_evidence(action: str) -> None:
    a = item('서울시 소상공인 미등록대부 '+action, '대부')
    b = item('서울시 전통시장 불법사채 집중 단속', '대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b) == 'enforcement:서울시:small_business'
    pair(a, b, True)


@pytest.mark.parametrize('nominal', [
    '단속을 실시계획에 포함', '단속이 시작점인 조례', '수사의 결과론 비판',
    '수사기관', '단속기관', '바로잡는다', '붙잡는다', '단속에 관한 조례',
])
def test_a_bounded_context_and_old_negatives(nominal: str) -> None:
    a = item('서울시 소상공인 불법사금융 '+nominal, '대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None
    pair(a, item('서울시 전통시장 불법대부 집중 단속', '대부'), False)


def test_a_description_cannot_supply_predicate() -> None:
    a = item('서울시 소상공인 불법사금융 단속의 절차 개정', '대부', '단속을 실시하고 수사에 착수')
    assert ic._issue_fingerprint(a) is None
    pair(a, item('서울시 소상공인 불법사금융 집중 단속', '대부'), False)


def test_a_campaign_bridge_stays_blocked() -> None:
    a = item('서울시 3월 소상공인 미등록대부 단속을 실시', '대부')
    b = item('서울시 소상공인 불법사채 집중 단속', '대부')
    c = item('서울시 9월 소상공인 불법대부 단속이 시작됐다', '대부')
    pair(a, c, False)
    for order in permutations([a, b, c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_b_deposit_insurer_reports_industry_measurement() -> None:
    a = item('예금보험공사 보험사 킥스비율 200% 자본여력 감소')
    b = item('예보 보험사 지급여력비율 200% 자본여력 감소')
    fa, fb = [ic._build_cluster_features(t) for t in (a, b)]
    assert fa.metric_identities == fb.metric_identities == {'capital_adequacy_ratio'}
    assert fa.reported_metrics == fb.reported_metrics == {('capital_adequacy_ratio', '200')}
    assert fa.metric_period == fb.metric_period == (None, None)
    assert fa.metric_subjects == fb.metric_subjects == {'보험사'}
    assert not ic._conflicting_metric_subjects(fa, fb)
    assert not ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, True)
    assert '예금보험공사' in ic._extract_entities(a.article.title)
    assert '예보' not in ic._extract_entities(b.article.title)


@pytest.mark.parametrize('owner', ['삼성생명', '국민은행', '보험사', '생보사', '손보사', '은행권', '저축은행권'])
def test_b_reporter_does_not_replace_measured_owner(owner: str) -> None:
    assert ic._metric_subjects('예금보험공사 '+owner+' 킥스비율 200%') == {owner}


@pytest.mark.parametrize('other', ['삼성생명', '생보사', '손보사'])
def test_b_reporter_exclusion_does_not_collapse_scope(other: str) -> None:
    pair(item('예금보험공사 보험사 킥스비율 200% 감소'), item(other+' 지급여력비율 200% 감소'), False)


def test_b_reporter_exclusion_cannot_bypass_period_or_event_veto() -> None:
    pair(item('예금보험공사 보험사 3월 킥스비율 200% 감소'),
         item('예보 보험사 6월 지급여력비율 201% 감소'), False)
    pair(item('예금보험공사 보험사 킥스비율 200% 자본확충 완료'),
         item('예보 보험사 지급여력비율 201% 새 회계제도 대응 전략 발표'), False)