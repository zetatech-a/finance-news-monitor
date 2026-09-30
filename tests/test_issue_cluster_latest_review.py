"""Regressions for the Codex review of 3de826a (PR #85)."""
from __future__ import annotations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline.issue_cluster import (
    _build_cluster_features, _metric_subjects, _normalize_title,
    _requires_complete_compatibility, _should_cluster, cluster_tagged_articles,
)
from src.pipeline.tagger import TaggedArticle


def tagged(title: str, description: str = "", sector: str = "기타") -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": description,
                                      "url": "https://example.test/" + title}), [sector], [], [])


@pytest.mark.parametrize("short,full", [("KB손보", "KB손해보험"), ("DB손보", "DB손해보험")])
def test_metric_subject_company_aliases(short: str, full: str) -> None:
    a = tagged(f"{short} 킥스비율 200% 달성", sector="보험")
    b = tagged(f"{full} 지급여력비율 200% 달성", sector="보험")
    assert _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 1


@pytest.mark.parametrize("left,right", [
    ("KB손보", "DB손해보험"), ("삼성생명", "한화생명"),
    ("가온손보", "가온손해보험"),  # No speculative suffix-wide alias rule.
])
def test_aliases_do_not_equate_different_or_unverified_subjects(left: str, right: str) -> None:
    assert not _should_cluster(tagged(f"{left} 킥스비율 200% 달성", sector="보험"),
                               tagged(f"{right} 지급여력비율 200% 달성", sector="보험"))


def test_missing_and_ambiguous_metric_subjects_stay_conservative() -> None:
    assert not _metric_subjects(_normalize_title("킥스비율 200% 달성"))
    assert len(_metric_subjects(_normalize_title("KB손보 DB손보 킥스비율 200% 달성"))) == 2
    assert not _should_cluster(tagged("킥스비율 200%…요구자본 확충에 성공", sector="보험"),
                               tagged("지급여력비율 200%…새 회계제도 대응 능력 입증", sector="보험"))


@pytest.mark.parametrize("background_index", [0, 1, 2])
def test_description_only_lending_reference_preserves_non_lending_bridge(background_index: int) -> None:
    items = [tagged("가온전자 생산설비 증설계획 확정"),
             tagged("가온전자 생산설비 증설계획 확정 나래산업 경영권 인수계약 체결"),
             tagged("나래산업 경영권 인수계약 체결")]
    assert len(cluster_tagged_articles(items)) == 1
    items[background_index].article.description = "불법사금융 관련 사례도 언급됐다"
    assert len(cluster_tagged_articles(items)) == 1
    assert not any(_requires_complete_compatibility(_build_cluster_features(item)) for item in items)


@pytest.mark.parametrize("term", ["불법사금융", "불법대부", "불법추심", "대부광고", "대출광고"])
def test_headline_lending_evidence_retains_strict_scope(term: str) -> None:
    assert _requires_complete_compatibility(_build_cluster_features(tagged(f"{term} 피해자 지원 대책")))


@pytest.mark.parametrize("marker", ["단신", "금융 브리핑", "일정"])
def test_low_value_enforcement_veto(marker: str) -> None:
    a = tagged(f"{marker} 서울시 전통시장 불법사금융 집중 단속 발표", sector="대부")
    b = tagged(f"{marker} 서울시 전통시장 불법사금융 피해 지원 발표", sector="대부")
    assert not _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 2


@pytest.mark.parametrize("marker", ["단신", "금융 브리핑", "일정"])
def test_low_value_metric_subject_veto(marker: str) -> None:
    assert not _should_cluster(tagged(f"{marker} 삼성생명 킥스비율 200%", sector="보험"),
                               tagged(f"{marker} 한화생명 킥스비율 200%", sector="보험"))


@pytest.mark.parametrize("marker", ["단신", "금융 브리핑", "일정"])
def test_valid_low_value_same_event_still_clusters(marker: str) -> None:
    a = tagged(f"{marker} 서울시 전통시장 불법사금융 집중 단속 발표", sector="대부")
    b = tagged(f"{marker} 서울시 전통시장 불법사금융 집중 단속 실시", sector="대부")
    assert _should_cluster(a, b)
    assert len(cluster_tagged_articles([a, b])) == 1


def test_company_alias_does_not_trigger_low_value_veto() -> None:
    assert _should_cluster(tagged("단신 KB손보 킥스비율 200% 달성", sector="보험"),
                           tagged("단신 KB손해보험 킥스비율 200% 달성", sector="보험"))
