"""Three Codex correctness regressions against 26ec9fc."""
from __future__ import annotations

from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article, replay
from src.pipeline import issue_cluster as ic
from src.pipeline.relevance_filter import has_domain_anchor
from src.pipeline.relevance_score import matched_terms, relevance_score
from src.pipeline.tagger import TaggedArticle
from src.pipeline.text_matcher import contains_term


def tagged(title: str) -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": "", "url": "https://example.test/" + title}), ["보험"], [], [])


@pytest.mark.parametrize("prefix,subject", [
    ("삼성생명도", "삼성생명"), ("삼성생명을", "삼성생명"),
    ("삼성생명과", "삼성생명"), ("한화생명과", "한화생명"),
    ("현대해상화재와", "현대해상화재"), ("현대해상화재를", "현대해상화재"),
    ("KB손해보험은", "kb손해보험"), ("삼성생명이", "삼성생명"),
    ("삼성생명의", "삼성생명"), ("현대해상화재는", "현대해상화재"),
    ("현대해상화재가", "현대해상화재"),
])
def test_named_subject_particles(prefix: str, subject: str) -> None:
    # 과/와: the insurer discusses its own reported ratio; no second entity.
    owner = " 동사의" if prefix.endswith(("과", "와")) else ""
    assert ic._metric_subjects(ic._normalize_title(f"{prefix}{owner} 킥스비율 200% 기준 자본여력 분석")) == {subject}


@pytest.mark.parametrize("prefix", ["삼성생명이익", "삼성생명도약", "삼성생명과정", "삼성생명은abc", "KB손해보험의견", "국민은행이익"])
def test_named_subject_lexical_continuation(prefix: str) -> None:
    assert not ic._metric_subjects(ic._normalize_title(f"{prefix} 킥스비율 200% 분석"))


@pytest.mark.parametrize("check", ["conflict", "pair", "cluster"])
def test_named_subject_particle_false_merge(check: str) -> None:
    a = tagged("삼성생명도 킥스비율 200% 자본여력 감소")
    b = tagged("한화생명도 킥스비율 200% 자본여력 감소")
    if check == "conflict":
        assert ic._conflicting_metric_subjects(ic._build_cluster_features(a), ic._build_cluster_features(b))
    elif check == "pair":
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("text,term", [
    ("불법 대부까지 피해 확산", "불법대부"), ("미등록 대부까지 피해 확산", "미등록대부"),
    ("불법 대부부터 집중 단속", "불법대부"), ("미등록 대부부터 점검", "미등록대부"),
    ("불법 대부만 규제 대상", "불법대부"), ("미등록 대부조차 적발", "미등록대부"),
])
@pytest.mark.parametrize("check", ["matching", "decision"])
def test_lending_remaining_particles(text: str, term: str, check: str) -> None:
    row = {"title": text, "summary": "", "url": "https://example.test/particle", "prob": .8}
    article = make_article(row)
    compact = make_article(dict(row, title=text.replace(" 대부", "대부")))
    if check == "matching":
        assert contains_term(text, term)
        assert term in matched_terms(article)["hard"]
        assert has_domain_anchor(article)
        assert relevance_score(article) == relevance_score(compact) > 0
    else:
        assert len(replay([row])[0]) == 1


@pytest.mark.parametrize("prefix,term", [("불법", "불법대부"), ("미등록", "미등록대부")])
@pytest.mark.parametrize("suffix", ["도 토지거래", "abc", "123", "만기", "까지abc", "부터123", "조차도"])
def test_lending_suffix_boundaries(prefix: str, term: str, suffix: str) -> None:
    text = f"{prefix} 대부{suffix}"
    article = make_article({"title": text, "summary": "", "url": "https://example.test/noise"})
    assert not contains_term(text, term)
    assert term not in matched_terms(article)["hard"]
    assert not has_domain_anchor(article)


@pytest.mark.parametrize("suffix", ["%p", "% p", "%포인트", "% 포인트"])
@pytest.mark.parametrize("check", ["evidence", "conflict", "pair", "cluster"])
def test_change_reports_preserve_subject_safety(suffix: str, check: str) -> None:
    a = tagged(f"삼성생명 킥스비율 20{suffix} 하락 자본여력 감소")
    b = tagged(f"한화생명 킥스비율 20{suffix} 하락 자본여력 감소")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert not fa.reported_metrics and not fb.reported_metrics
    if check == "evidence":
        assert fa.metric_subjects == {"삼성생명"}
        assert fb.metric_subjects == {"한화생명"}
        assert fa.metric_identities == fb.metric_identities == {"capital_adequacy_ratio"}
    elif check == "conflict":
        assert ic._conflicting_metric_subjects(fa, fb)
    elif check == "pair":
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("suffix", ["%p", "% p", "%포인트", "% 포인트"])
def test_change_reports_preserve_period_safety(suffix: str) -> None:
    a = tagged(f"삼성생명 1분기 킥스비율 20{suffix} 하락")
    b = tagged(f"삼성생명 2분기 킥스비율 20{suffix} 하락")
    assert ic._conflicting_metric_periods(ic._build_cluster_features(a), ic._build_cluster_features(b))
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


def test_change_magnitude_cannot_authorize_exact_level_shortcut() -> None:
    a = tagged("KB손보 지급여력비율 20%포인트 하락")
    b = tagged("KB손해보험 킥스 비율 20% 경영개선 권고")
    assert not ic._build_cluster_features(a).reported_metrics
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


def test_absolute_level_shortcut_survives() -> None:
    assert len(ic.cluster_tagged_articles([tagged("삼성생명 킥스비율 200%"),
                                           tagged("삼성생명 지급여력비율 200.0%")])) == 1


@pytest.mark.parametrize("title", [
    "삼성생명 킥스비율 200%, 한화생명 킥스비율 20%p 하락",
    "삼성생명 킥스비율 20%p 하락, 한화생명 킥스비율 200%",
    "삼성생명 킥스비율 20%p 하락, 한화생명 킥스비율 20%p 하락",
])
def test_change_and_level_occurrences_keep_ambiguous_subjects_disabled(title: str) -> None:
    assert not ic._metric_subjects(ic._normalize_title(title))


def test_change_subject_conflict_cannot_bridge() -> None:
    a = tagged("삼성생명도 킥스비율 20%p 하락 자본여력 감소")
    bridge = tagged("킥스비율 20%p 하락 자본여력 감소")
    c = tagged("한화생명도 킥스비율 20%p 하락 자본여력 감소")
    assert ic._should_cluster(a, bridge) and ic._should_cluster(bridge, c)
    for order in permutations([a, bridge, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id

@pytest.mark.parametrize("label,identity", [("연체율", "delinquency_rate"), ("예대금리차", "loan_deposit_spread")])
def test_other_supported_metric_change_identities(label: str, identity: str) -> None:
    a = tagged(f"국민은행 {label} 2%p 상승")
    b = tagged(f"신한은행 {label} 2%p 상승")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert fa.metric_identities == fb.metric_identities == {identity}
    assert not fa.reported_metrics and not fb.reported_metrics
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("period", ["6월말", "상반기", ""])
def test_same_or_missing_change_period_keeps_wire_recall(period: str) -> None:
    a = tagged("삼성생명 2분기 킥스비율 20%p 하락 자본여력 감소")
    b = tagged(f"삼성생명 {period} 킥스비율 20%p 하락 자본여력 감소")
    assert not ic._conflicting_metric_periods(ic._build_cluster_features(a), ic._build_cluster_features(b))
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_historical_particle_mode_is_preserved_and_restored() -> None:
    from unittest.mock import patch
    from src.pipeline import text_matcher as matcher
    text = "미등록 대부까지 피해 확산"
    with patch.dict(matcher._ALIAS_MATCH_MODES, {"미등록 대부": "korean_particle"}):
        assert not contains_term(text, "미등록대부")
    assert contains_term(text, "미등록대부")


def test_named_do_and_lending_do_have_distinct_contracts() -> None:
    assert ic._metric_subjects(ic._normalize_title("삼성생명도 킥스비율 20%p 하락")) == {"삼성생명"}
    assert not contains_term("불법 대부도 토지거래", "불법대부")
