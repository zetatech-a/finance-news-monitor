"""Minimal reproductions for PR #85 automated review, plus positive controls."""
import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline.issue_cluster import (
    _issue_fingerprint, _normalize_title, _reported_metrics, _should_cluster,
)
from src.pipeline.tagger import TaggedArticle


def tagged(title, description="", sector="대부"):
    return TaggedArticle(make_article({"title": title, "summary": description,
                                      "url": "https://example.test/" + title}), [sector], [], [])


def test_enforcement_background_does_not_identify_policy_event():
    enforcement = tagged("서울시 전통시장 불법사금융 집중 단속")
    policy = tagged("시민단체 불법사금융 피해지원 제도 개선안 제출",
                    "서울시 전통시장 불법사금융 집중 단속 사례도 소개했다.")
    assert _issue_fingerprint(policy) is None
    assert not _should_cluster(enforcement, policy)


@pytest.mark.parametrize("word", ["필요시", "검토시", "대출시"])
def test_conditional_words_are_not_local_authorities(word):
    assert _issue_fingerprint(tagged(f"전통시장 불법사금융 {word} 집중 단속")) is None
    plain = tagged("서울시 전통시장 불법사금융 집중 단속")
    with_condition = tagged(f"서울시 전통시장 불법사금융 집중 단속 {word} 추가 조사")
    assert _issue_fingerprint(plain) == _issue_fingerprint(with_condition)


@pytest.mark.parametrize("left,right", [
    ("삼성생명 킥스비율 200%", "한화생명 지급여력비율 200%"),
    ("삼성생명 킥스비율 200%", "한화생명 킥스비율 200%"),
    ("가온보험 킥스비율 200%", "나래보험 지급여력비율 200%"),
])
def test_equal_metric_value_does_not_override_different_subjects(left, right):
    assert not _should_cluster(tagged(left, sector="보험"), tagged(right, sector="보험"))


@pytest.mark.parametrize("label", ["K-ICS 비율", "K-ICS비율", "킥스 비율", "킥스비율", "지급여력 비율"])
def test_spaced_metric_labels_preserve_same_event(label):
    title = f"보험사 {label} 215.2%"
    assert _reported_metrics(_normalize_title(title)) == {("capital_adequacy_ratio", "215.2")}
    assert _should_cluster(tagged(title, sector="보험"),
                           tagged("보험사 2분기 킥스 215.2%...자본 여력 감소", sector="보험"))


def test_named_metric_subject_can_cluster_spaced_wire_variant():
    assert _should_cluster(tagged("삼성생명 K-ICS 비율 200%", sector="보험"),
                           tagged("삼성생명은 지급여력비율 200% 달성", sector="보험"))


def test_metric_comparison_after_value_does_not_change_subject():
    assert _should_cluster(tagged("보험사 K-ICS 비율 215.2%...생보사 하락 손보사 상승", sector="보험"),
                           tagged("보험사 6월말 킥스 215.2%", sector="보험"))


def test_shared_regulator_does_not_override_metric_subject_conflict():
    assert not _should_cluster(tagged("금감원 삼성생명 킥스비율 200% 확인", sector="보험"),
                               tagged("금감원 한화생명 킥스비율 200% 확인", sector="보험"))


def test_unknown_metric_subject_does_not_authorize_shortcut():
    from src.pipeline.issue_cluster import _build_cluster_features
    left = tagged("킥스비율 200%…요구자본 확충에 성공", sector="보험")
    right = tagged("지급여력비율 200%…새 회계제도 대응 능력 입증", sector="보험")
    assert not _build_cluster_features(left).metric_subjects
    assert not _build_cluster_features(right).metric_subjects
    assert not _should_cluster(left, right)


def test_metric_subject_conflict_cannot_bridge_through_unknown_subject():
    from itertools import permutations
    from src.pipeline.issue_cluster import cluster_tagged_articles
    a = tagged("삼성생명 킥스비율 200% 자본 확충", sector="보험")
    b = tagged("킥스비율 200% 자본 확충", sector="보험")
    c = tagged("한화생명 킥스비율 200% 자본 확충", sector="보험")
    assert _should_cluster(a, b) and _should_cluster(b, c)
    for order in permutations([a, b, c]):
        cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id


def test_explicit_city_hall_and_police_authorities_still_work():
    for authority in ("수원시청", "경기남부경찰청", "서울특별시", "부산광역시"):
        assert _issue_fingerprint(tagged(f"{authority} 전통시장 불법사금융 집중 단속"))
    assert _issue_fingerprint(tagged("서울특별시 전통시장 불법사금융 단속")) == \
        _issue_fingerprint(tagged("서울시 전통시장 불법사금융 단속"))


def test_non_lending_single_link_and_input_order_are_preserved():
    from src.pipeline.issue_cluster import cluster_tagged_articles
    a = tagged("가온전자 생산설비 증설계획 확정", sector="기타")
    b = tagged("가온전자 생산설비 증설계획 확정 나래산업 경영권 인수계약 체결", sector="기타")
    c = tagged("나래산업 경영권 인수계약 체결", sector="기타")
    assert _should_cluster(a, b) and _should_cluster(b, c)
    assert not _should_cluster(a, c)
    assert len(cluster_tagged_articles([a, b, c])) == 1
