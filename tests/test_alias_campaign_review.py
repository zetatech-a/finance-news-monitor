"""Remaining insurer alias, complete metric label and campaign-year review."""
from __future__ import annotations

from itertools import permutations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle


def tagged(title: str, sector: str = "보험", summary: str = "") -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": summary,
                                      "url": "https://example.test/" + title}), [sector], [], [])


@pytest.mark.parametrize("full,short", [
    ("NH농협손해보험", "NH농협손보"), ("KB손해보험", "KB손보"), ("DB손해보험", "DB손보"),
])
@pytest.mark.parametrize("check", ["subject", "pair", "cluster"])
def test_verified_insurer_aliases(full: str, short: str, check: str) -> None:
    a = tagged(f"{full} 킥스비율 200% 자본여력 감소")
    b = tagged(f"{short} 지급여력비율 200% 자본여력 감소")
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    if check == "subject":
        assert fa.metric_subjects == fb.metric_subjects == {full.lower()}
        assert not ic._conflicting_metric_subjects(fa, fb)
        assert fa.reported_metrics & fb.reported_metrics
    elif check == "pair":
        assert ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 1


@pytest.mark.parametrize("left,right", [
    ("NH농협손해보험", "KB손해보험"), ("NH농협손보", "DB손해보험"),
    ("NH농협손보", "손보사"), ("삼성생명", "한화생명"), ("삼성생명", "생보사"),
    ("가상손보", "가상손해보험"),
])
def test_different_insurers_and_unverified_aliases_stay_distinct(left: str, right: str) -> None:
    a, b = [tagged(f"{name} 킥스비율 200% 자본여력 감소") for name in (left, right)]
    assert ic._conflicting_metric_subjects(ic._build_cluster_features(a), ic._build_cluster_features(b))
    assert not ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("title", ["A사 킥스타터 펀딩 성공", "B사 킥스타터 게임 출시",
                                    "킥스타터 프로젝트 공개", "킥스테 프로젝트 공개",
                                    "킥스비율도약 프로젝트", "킥스비율과정 분석"])
def test_kickstarter_is_not_a_capital_ratio(title: str) -> None:
    feature = ic._build_cluster_features(tagged(title, "기타"))
    assert "capital_adequacy_ratio" not in feature.issue_terms
    assert not feature.metric_identities and not feature.reported_metrics


@pytest.mark.parametrize("check", ["pair", "cluster"])
def test_unrelated_kickstarter_projects_stay_separate(check: str) -> None:
    a, b = [tagged(t, "기타") for t in ("A사 킥스타터 펀딩 성공", "B사 킥스타터 게임 출시")]
    if check == "pair":
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("label", ["킥스비율", "킥스 비율", "킥스", "K-ICS 비율", "K ICS 비율",
                                    "지급여력비율", "지급여력 비율"])
def test_complete_metric_labels_keep_extraction_and_wire_recall(label: str) -> None:
    a = tagged(f"KB손보 2분기 {label} 200%")
    b = tagged("KB손해보험 6월말 지급여력비율 200.00%")
    f = ic._build_cluster_features(a)
    assert "capital_adequacy_ratio" in f.issue_terms
    assert f.metric_identities == {"capital_adequacy_ratio"}
    assert f.reported_metrics == {("capital_adequacy_ratio", "200")}
    assert f.metric_subjects == {"kb손해보험"}
    assert f.metric_period == (None, 6)
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def campaign(year: str, variant: bool = False, prefix: str = "") -> TaggedArticle:
    event = "소상공인 불법대부 특별 수사" if variant else "전통시장 불법사금융 집중 단속"
    return tagged(f"{prefix}서울시 {year} {event}", "대부")


@pytest.mark.parametrize("check", ["veto", "pair", "cluster"])
@pytest.mark.parametrize("prefix", ["", "단신 "])
def test_explicit_campaign_year_conflict(check: str, prefix: str) -> None:
    a, b = campaign("2025년", prefix=prefix), campaign("2026년", True, prefix)
    fa, fb = ic._build_cluster_features(a), ic._build_cluster_features(b)
    assert fa.fingerprint == fb.fingerprint == "enforcement:서울시:small_business"
    if check == "veto":
        assert ic._conflicting_enforcement_periods(fa, fb)
    elif check == "pair":
        assert not ic._should_cluster(a, b)
    else:
        assert len(ic.cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("year", ["2026년", ""])
def test_same_or_missing_campaign_year_keeps_wires(year: str) -> None:
    a, b = campaign("2026년"), campaign(year, True)
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_missing_campaign_year_cannot_bridge_conflicting_years() -> None:
    a, bridge, c = campaign("2025년"), campaign(""), campaign("2026년", True)
    assert ic._should_cluster(a, bridge) and ic._should_cluster(bridge, c)
    for order in permutations([a, bridge, c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id


def test_description_background_year_does_not_create_campaign_conflict() -> None:
    a, b = campaign("2026년"), campaign("")
    b.article.description = "2025년 단속 사례를 배경으로 설명했다"
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1


def test_ambiguous_headline_years_do_not_infer_campaign_period() -> None:
    a, b = campaign("2025년 2026년"), campaign("2026년", True)
    assert ic._should_cluster(a, b)
    assert len(ic.cluster_tagged_articles([a, b])) == 1

@pytest.mark.parametrize("phrase", ["킥스비율도 안정적으로 유지", "킥스비율을 방어", "킥스비율과 기본자본비율"])
def test_corpus_metric_label_particles_keep_issue_evidence(phrase: str) -> None:
    # September 16–17 candidate snippets: full particle, not a word prefix.
    item = tagged("보험사 자본관리 현황", summary=phrase)
    assert "capital_adequacy_ratio" in ic._build_cluster_features(item).issue_terms
