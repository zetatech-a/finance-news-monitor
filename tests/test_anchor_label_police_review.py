"""Relevance spelling parity, metric labels and police authority review."""
from __future__ import annotations
from itertools import permutations
import pytest
from scripts.evaluate_loan_news import make_article, replay
from src.pipeline import issue_cluster as ic, relevance_score as rs
from src.pipeline.relevance_filter import has_domain_anchor
from src.pipeline.tagger import TaggedArticle


def row(title: str, prob: float = .1) -> dict:
    return {"title": title, "summary": "", "url": "https://example.test/" + title, "prob": prob}


def tagged(title: str, sector: str = "보험") -> TaggedArticle:
    return TaggedArticle(make_article(row(title)), [sector], [], [])


@pytest.mark.parametrize("prefix,compound,action", [
    ("불법", "대부업", "점검"), ("미등록", "대부업", "점검"),
    ("불법", "대부중개업", "단속"), ("미등록", "대부중개업", "적발"),
])
@pytest.mark.parametrize("check", ["evidence", "decision"])
def test_lending_compound_parity(prefix: str, compound: str, action: str, check: str) -> None:
    compact, spaced = f"{prefix}{compound} {action}", f"{prefix} {compound} {action}"
    a,b = make_article(row(compact)),make_article(row(spaced))
    if check == "evidence":
        ma,mb = rs.matched_terms(a),rs.matched_terms(b)
        assert ma["hard"] == mb["hard"]
        assert sum(rs._WEIGHTS.hard[t] for t in ma["hard"]) == sum(rs._WEIGHTS.hard[t] for t in mb["hard"])
        assert rs.relevance_score(a) == rs.relevance_score(b)
        assert rs._has_strong_finance_anchor(compact) == rs._has_strong_finance_anchor(spaced) == True
        assert has_domain_anchor(a) == has_domain_anchor(b) == True
    else:
        assert bool(replay([row(compact)])[0]) == bool(replay([row(spaced)])[0])


@pytest.mark.parametrize("prefix", ["불법", "미등록"])
def test_compact_baseline_and_high_probability_recall(prefix: str) -> None:
    for space in ("", " "):
        title=f"{prefix}{space}대부업 점검"
        article=make_article(row(title))
        assert rs.matched_terms(article)["hard"] == [prefix+"대부"]
        assert rs.relevance_score(article) == 6
        assert not replay([row(title,.1)])[0]
        assert replay([row(title,.8)])[0]


@pytest.mark.parametrize("title,anchors", [
    ("금융위 대부업 점검", {"금융위","대부업"}),
    ("보험사 연체율 상승", {"보험사","연체율"}),
    ("불법 대부업 점검 및 대부업 제도 논의", {"불법대부","대부업"}),
    ("미등록 대부업 점검 및 대부업체 조사", {"미등록대부","대부업"}),
])
def test_independent_anchors_survive(title: str, anchors: set[str]) -> None:
    assert anchors <= set(rs.matched_terms(make_article(row(title)))["hard"])


@pytest.mark.parametrize("title", ["불법 대부 피해","미등록 대부 영업","대부도 여행","공유재산 대부","대부료"])
def test_existing_anchor_noise_decisions(title: str) -> None:
    expected=title.startswith(("불법", "미등록"))
    assert has_domain_anchor(make_article(row(title))) == expected
    assert bool(replay([row(title,.8)])[0]) == expected


@pytest.mark.parametrize("label", ["킥스비율은", "킥스비율이", "킥스비율도", "킥스비율만",
    "킥스", "킥스비율", "킥스 비율", "K-ICS", "K ICS", "KICS", "KICS 비율", "지급여력비율", "지급여력 비율"])
def test_metric_label_full_feature_path(label: str) -> None:
    a=tagged(f"삼성생명 2분기 {label} 200%")
    f=ic._build_cluster_features(a)
    assert f.metric_identities == {"capital_adequacy_ratio"}
    assert f.reported_metrics == {("capital_adequacy_ratio","200")}
    assert f.metric_subjects == {"삼성생명"}
    assert f.metric_period == (None,6)
    assert "capital_adequacy_ratio" in f.issue_terms
    b=tagged("삼성생명 상반기 지급여력비율 200.00% 자본 확충")
    assert ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 1


@pytest.mark.parametrize("label", ["킥스비율도", "킥스비율만", "KICS", "KICS 비율"])
@pytest.mark.parametrize("check", ["subject", "pair", "cluster"])
def test_metric_label_subject_veto(label: str, check: str) -> None:
    a,b=[tagged(f"{company} {label} 200% 자본여력 감소") for company in ("삼성생명","한화생명")]
    if check == "subject":
        assert ic._conflicting_metric_subjects(ic._build_cluster_features(a),ic._build_cluster_features(b))
    elif check == "pair":
        assert not ic._should_cluster(a,b)
    else:
        assert len(ic.cluster_tagged_articles([a,b])) == 2


@pytest.mark.parametrize("label", ["킥스비율도","킥스비율만","KICS"])
@pytest.mark.parametrize("suffix", ["%","%p"])
def test_metric_label_period_and_change_safety(label: str, suffix: str) -> None:
    a=tagged(f"삼성생명 1분기 {label} 200{suffix} 자본여력 감소")
    b=tagged(f"삼성생명 2분기 {label} 201{suffix} 자본여력 감소")
    fa,fb=ic._build_cluster_features(a),ic._build_cluster_features(b)
    assert ic._conflicting_metric_periods(fa,fb)
    if suffix == "%p":
        assert not fa.reported_metrics and not fb.reported_metrics
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2


@pytest.mark.parametrize("label", ["SKICS","KICSabc","myKICSvalue","킥스타터","킥스비율도입","킥스비율만기"])
def test_metric_label_lexical_negatives(label: str) -> None:
    f=ic._build_cluster_features(tagged(f"삼성생명 {label} 200%"))
    assert not f.metric_identities and not f.reported_metrics and not f.metric_subjects
    assert "capital_adequacy_ratio" not in f.issue_terms


def test_mixed_new_metric_labels_remain_ambiguous() -> None:
    f=ic._build_cluster_features(tagged("삼성생명 KICS 200%, 한화생명 킥스비율도 180%"))
    assert not f.metric_subjects


@pytest.mark.parametrize("city,variants", [
    ("서울", ("서울경찰청","서울시경찰청","서울특별시경찰청")),
    ("부산", ("부산경찰청","부산시경찰청","부산광역시경찰청")),
])
@pytest.mark.parametrize("check", ["fingerprint","pair","cluster"])
def test_police_authority_variants(city: str, variants: tuple[str,...], check: str) -> None:
    items=[tagged(f"{name} 소상공인 불법사금융 집중 단속","대부") for name in variants]
    if check == "fingerprint":
        assert {ic._issue_fingerprint(t) for t in items} == {f"enforcement:{city}경찰청:small_business"}
    elif check == "pair":
        assert all(ic._should_cluster(a,b) for a,b in permutations(items,2))
    else:
        assert len(ic.cluster_tagged_articles(items)) == 1


@pytest.mark.parametrize("other", ["부산경찰청","서울시","서울시청"])
def test_police_distinct_from_other_authorities(other: str) -> None:
    a,b=[tagged(f"{name} 소상공인 불법사금융 집중 단속","대부") for name in ("서울경찰청",other)]
    assert ic._issue_fingerprint(a) != ic._issue_fingerprint(b)
    assert not ic._should_cluster(a,b)
    assert len(ic.cluster_tagged_articles([a,b])) == 2


def test_police_alias_year_safety_and_bridge() -> None:
    a=tagged("서울경찰청 2025년 소상공인 불법사금융 집중 단속","대부")
    b=tagged("서울시경찰청 소상공인 불법사금융 집중 단속","대부")
    c=tagged("서울특별시경찰청 2026년 소상공인 불법사금융 집중 단속","대부")
    assert ic._should_cluster(a,b) and ic._should_cluster(b,c)
    assert not ic._should_cluster(a,c)
    for order in permutations([a,b,c]):
        assert len(ic.cluster_tagged_articles(list(order))) == 2
        assert a.article.cluster_id != c.article.cluster_id