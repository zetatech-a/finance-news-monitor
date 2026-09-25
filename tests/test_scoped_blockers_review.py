"""Four scoped blockers from the a353a3c review; industry particles are deferred."""
from __future__ import annotations

from dataclasses import replace
from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def item(title: str, sector: str = '보험') -> TaggedArticle:
    return TaggedArticle(make_article({'title': title, 'summary': '', 'url': 'https://example.test/'+title}), [sector], [], [])


def pair(a: TaggedArticle, b: TaggedArticle, expected: bool) -> None:
    assert ic._should_cluster(a, b) is expected
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if expected else 2)


@pytest.mark.parametrize('prefix,wire,subject', [
    ('삼성생명 등 10개 보험사', '10개 보험사', '보험사'),
    ('삼성생명·한화생명 등 10개 생보사', '10개 생보사', '생보사'),
    ('KB손해보험을 포함한 10개 손보사', '10개 손보사', '손보사'),
])
def test_a_quantified_aggregate_wire(prefix: str, wire: str, subject: str) -> None:
    a = item(prefix+' 킥스비율 215.2% 자본여력 감소')
    b = item(wire+' 지급여력비율 215.2% 자본여력 감소')
    fa, fb = [ic._build_cluster_features(t) for t in (a,b)]
    assert fa.metric_subjects == fb.metric_subjects == {subject}
    assert not ic._conflicting_metric_subjects(fa,fb)
    pair(a,b,True)


@pytest.mark.parametrize('prefix,expected', [
    ('삼성생명 등 새 정책 추진 보험사', {'삼성생명'}),
    ('삼성생명 등 10개사 보험사', {'삼성생명'}),
    ('10개 보험사 중 삼성생명', {'삼성생명'}),
    ('삼성생명과 한화생명', {'삼성생명','한화생명'}),
])
def test_a_no_arbitrary_modifier_bridge(prefix: str, expected: set[str]) -> None:
    assert ic._metric_subjects(ic._normalize_title(prefix+' KICS 215.2%')) == expected


@pytest.mark.parametrize('left,right', [
    ('삼성생명 등 10개 보험사','10개 생보사'),
    ('삼성생명 등 10개 보험사','10개 손보사'),
    ('삼성생명 등 10개 생보사','10개 손보사'),
    ('은행권','저축은행권'),
    ('삼성생명','10개 보험사'),
])
def test_a_distinct_scopes(left: str, right: str) -> None:
    pair(item(left+' KICS 215.2% 감소'),item(right+' KICS 215.2% 감소'),False)


def test_b_locative_compound_wire() -> None:
    a=item('삼성생명 킥스비율 200% 자본확충')
    b=item('삼성생명 지급여력비율 200.0% 자본 확충에 나선다')
    fa,fb=[ic._build_cluster_features(t) for t in (a,b)]
    assert '자본확충' in ic._metric_event_comparison_units(fb)
    assert not ic._metric_match_lacks_event_evidence(fa,fb)
    pair(a,b,True)
    # Comparison overlap must not authorize an otherwise unsupported merge.
    assert not ic._should_cluster_features(*[replace(f,issue_terms=set(),entities=set(),numbers=set()) for f in (fa,fb)])
    assert '확충에' in fb.tokens and '확충' not in fb.tokens


@pytest.mark.parametrize('word,stem', [('확충에','확충'),('구조에','구조')])
def test_b_single_bounded_locative(word: str, stem: str) -> None:
    assert stem in ic._metric_event_token_variants(word)


@pytest.mark.parametrize('word,forbidden', [('확충에는','확충'),('확충에게','확충'),('확충에도','확충'),('확충에에','확충'),('집에','집')])
def test_b_no_recursive_or_short_stemming(word: str, forbidden: str) -> None:
    assert forbidden not in ic._metric_event_token_variants(word)


def test_b_p1_distinct_events_and_bridge() -> None:
    a=item('삼성생명 킥스비율 200% 자본확충 완료')
    b=item('삼성생명 지급여력비율 200%')
    c=item('삼성생명 지급여력비율 200% 새 회계제도 대응 전략 발표')
    assert ic._metric_match_lacks_event_evidence(ic._build_cluster_features(a),ic._build_cluster_features(c))
    pair(a,c,False)
    for order in permutations([a,b,c]):
        ic.cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_c_regulator_fallback_wire() -> None:
    a=item('한국은행, 은행권 연체율 10% 상승','은행')
    b=item('한은, 은행권 연체율 10% 상승','은행')
    fa,fb=[ic._build_cluster_features(t) for t in (a,b)]
    assert fa.metric_subjects == fb.metric_subjects == {'은행권'}
    assert not ic._conflicting_metric_subjects(fa,fb)
    pair(a,b,True)
    assert '한국은행' in ic._extract_entities(ic._normalize_title(a.article.title))


@pytest.mark.parametrize('regulator',['한국은행','금융감독원','금융위원회'])
def test_c_exclusions_leave_industry_available(regulator: str) -> None:
    assert ic._metric_subjects(regulator+' 저축은행권 연체율 10% 상승') == {'저축은행권'}


@pytest.mark.parametrize('bank',['국민은행','신한은행','우리은행','하나은행'])
def test_c_commercial_bank_untouched(bank: str) -> None:
    a=item(bank+' 연체율 10% 상승','은행')
    b=item(bank+' 연체율 10.0% 상승','은행')
    assert ic._metric_subjects(ic._normalize_title(a.article.title)) == {bank}
    pair(a,b,True)
    pair(a,item('은행권 연체율 10% 상승','은행'),False)


@pytest.mark.parametrize('agency',['수사기관','단속기관','수사기관이나','단속기관의'])
def test_d_agency_mention_not_action(agency: str) -> None:
    title=f'서울시 소상공인 불법사금융 {agency} 인권교육 지원 조례 개정'
    a=item(title,'대부')
    b=item('서울시 소상공인 불법사금융 집중 단속','대부')
    assert not ic._is_enforcement_headline(title)
    assert ic._issue_fingerprint(a) is None
    pair(a,b,False)


@pytest.mark.parametrize('action',[
    '단속','수사','집중단속','특별단속','단속에 나선다','단속을 실시',
    '단속이 시작됐다','수사의 결과','단속해 적발','단속한다',
    '수사개시','수사의뢰','잡는다',
])
def test_d_existing_action_uses(action: str) -> None:
    a=item('서울시 소상공인 불법사금융 '+action,'대부')
    b=item('서울시 소상공인 불법사금융 집중 단속','대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == ic._issue_fingerprint(b)
    pair(a,b,True)


@pytest.mark.parametrize('non_action',['바로잡는다','붙잡는다','다잡는다','수사abc','단속123'])
def test_d_non_actions_stay_out(non_action: str) -> None:
    a=item('서울시 소상공인 불법사금융 '+non_action,'대부')
    assert not ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) is None


def test_d_agency_mention_does_not_hide_separate_action() -> None:
    a=item('서울시 소상공인 불법사금융 수사기관 합동 집중 단속','대부')
    assert ic._is_enforcement_headline(a.article.title)
    assert ic._issue_fingerprint(a) == 'enforcement:서울시:small_business'
