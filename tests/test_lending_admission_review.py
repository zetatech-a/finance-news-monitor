"""Bounded lending admission: corroborated wire recall without snippet-only bridges.

Real rows in tests/fixtures/loan_news/admission_events.json are exact stored
candidate titles/snippets/URLs (reports/_candidates, 2026-06-30 to 2026-08-21)
with the sector the replay pipeline assigned. Events were labelled by reading
headlines and snippets; unlabelled same-day articles are not included.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline.tagger import TaggedArticle

FIXTURE = Path("tests/fixtures/loan_news/admission_events.json")
SNIPPET = ("서울 강북경찰서는 전기통신사업법과 대부업법 위반 혐의로 조직원 9명을 입건하고 "
           "이 가운데 6명을 구속했다고 밝혔다 이들은 소액대출을 미끼로 선불유심을 개통하게 했다")


def item(title: str, sector: str = "대부", summary: str = "") -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": summary,
                                      "url": "https://example.test/" + title}), [sector], [], [])


def cluster_count(items: list[TaggedArticle]) -> int:
    return len(ic.cluster_tagged_articles(items))


@pytest.fixture
def supports(monkeypatch: pytest.MonkeyPatch) -> tuple[set, set]:
    """Declare each pair's support kind so only admission counting is exercised."""
    pair: set[frozenset[str]] = set()
    snippet: set[frozenset[str]] = set()
    monkeypatch.setattr(ic, "_should_cluster_features",
                        lambda a, b: frozenset((a.norm_title, b.norm_title)) in pair)
    monkeypatch.setattr(ic, "_description_corroborates",
                        lambda a, b: frozenset((a.norm_title, b.norm_title)) in snippet)
    return pair, snippet


# Lending headlines are admitted in title order: 가 -> 나 -> 다.
A, B, C = "가 기사", "나 기사", "다 기사"


def test_snippet_only_support_cannot_join_single_member_cluster(supports) -> None:
    _, snippet = supports
    snippet.add(frozenset((A, B)))
    assert cluster_count([item(A), item(B)]) == 2


def test_two_snippet_supports_without_pair_support_are_rejected(supports) -> None:
    pair, snippet = supports
    pair.add(frozenset((A, B)))
    snippet.update({frozenset((C, A)), frozenset((C, B))})
    assert cluster_count([item(A), item(B), item(C)]) == 2


def test_pair_support_plus_distinct_snippet_support_is_admitted(supports) -> None:
    pair, snippet = supports
    pair.update({frozenset((A, B)), frozenset((C, A))})
    snippet.add(frozenset((C, B)))
    assert cluster_count([item(A), item(B), item(C)]) == 1


def test_one_member_with_pair_and_snippet_support_counts_once(supports) -> None:
    pair, snippet = supports
    pair.update({frozenset((A, B)), frozenset((C, A))})
    snippet.add(frozenset((C, A)))
    assert cluster_count([item(A), item(B), item(C)]) == 2


def test_non_lending_admission_keeps_single_link_without_snippets(monkeypatch, supports) -> None:
    pair, _ = supports
    pair.update({frozenset((A, B)), frozenset((B, C))})

    def unexpected(a, b):
        raise AssertionError("snippet corroboration is lending-only")

    monkeypatch.setattr(ic, "_description_corroborates", unexpected)
    assert cluster_count([item(A, "은행"), item(B, "은행"), item(C, "은행")]) == 1


def corroborating_pair() -> tuple[ic._ClusterFeatures, ic._ClusterFeatures]:
    return (ic._build_cluster_features(item("대포유심 4185개 불법사금융 조직 검거", summary=SNIPPET)),
            ic._build_cluster_features(item("유심 1개당 10만원 대출 불법사금융 일당 구속", summary=SNIPPET)))


def test_bounded_snippet_corroboration_positive_control() -> None:
    a, b = corroborating_pair()
    assert ic._description_corroborates(a, b)


@pytest.mark.parametrize("left,right", [
    ("finance:delinquent_debt_purchase", "rule:illegal_loan_ad_crackdown"),
    ("enforcement:서울시:small_business", None),
    ("enforcement:서울시:small_business", "enforcement:부산시:small_business"),
])
def test_fingerprint_conflicts_never_corroborate(left, right) -> None:
    a, b = corroborating_pair()
    assert not ic._description_corroborates(replace(a, fingerprint=left), replace(b, fingerprint=right))
    assert ic._description_corroborates(replace(a, fingerprint=left), replace(b, fingerprint=left))


def test_snippets_need_same_sector_non_low_value_and_shared_headline_term() -> None:
    a, b = corroborating_pair()
    assert not ic._description_corroborates(a, replace(b, sector="은행"))
    assert not ic._description_corroborates(a, replace(b, low_value=True))
    roundup = ic._build_cluster_features(item("[연합뉴스 이 시각 헤드라인] - 14:30", summary=SNIPPET))
    assert not ic._description_corroborates(a, roundup)
    assert not ic._description_corroborates(a, replace(b, description_tokens=set()))
    unrelated = ic._build_cluster_features(item("불법사금융 조직 검거", summary="서민금융진흥원 캠페인 참여"))
    assert not ic._description_corroborates(a, unrelated)


def real_clusters(date: str) -> tuple[list[dict], list[str]]:
    rows = [row for row in json.loads(FIXTURE.read_text(encoding="utf-8")) if row["date"] == date]
    items = [TaggedArticle(make_article(row), [row["sector"]], [], []) for row in rows]
    ic.cluster_tagged_articles(items)
    return rows, [entry.article.cluster_id for entry in items]


def largest_share(date: str, event: str) -> tuple[int, int]:
    rows, cids = real_clusters(date)
    members = Counter(cid for row, cid in zip(rows, cids) if row["event"] == event)
    return max(members.values()), sum(members.values())


@pytest.mark.parametrize("date,event,minimum", [
    # Complete-link kept the largest card at 4 of 18 and 2 of 15 wire variants.
    ("2026-07-02", "fsc_decree", 15),
    ("2026-08-21", "gangbuk_usim", 12),
])
def test_real_wire_variants_recover_one_card(date, event, minimum) -> None:
    largest, total = largest_share(date, event)
    assert largest >= minimum, (largest, total)


def test_inclusive_finance_policy_does_not_join_sns_bill_card() -> None:
    rows, cids = real_clusters("2026-06-30")
    sns = {cid for row, cid in zip(rows, cids) if row["event"] == "sns_bill"}
    assert len(sns) == 1
    joined = [row["title"] for row, cid in zip(rows, cids) if cid in sns and row["event"] != "sns_bill"]
    # These two already pass the unchanged production pair rule (HEAD behavior).
    assert sorted(joined) == sorted([
        "은행 '포용금융' 평가체계 만든다…정책서민금융 전면 개편 착수",
        "금융위, 정책서민금융 재설계 착수…금융사 '포용금융 평가' 도입 논의",
    ])
    # The rejected candidate is reachable only through snippet support.
    candidate = next(row for row in rows if row["title"].startswith("중·저신용자 외면 못하게"))
    features = ic._build_cluster_features(TaggedArticle(make_article(candidate), [candidate["sector"]], [], []))
    members = [ic._build_cluster_features(TaggedArticle(make_article(row), [row["sector"]], [], []))
               for row, cid in zip(rows, cids) if cid in sns]
    assert not any(ic._should_cluster_features(features, member) for member in members)
    assert sum(ic._description_corroborates(features, member) for member in members) >= 2


@pytest.mark.parametrize("date,event,others", [
    ("2026-07-23", "police_plan", ["saedoyak", "roundup", "column"]),
    ("2026-08-21", "gangbuk_usim", ["jung_campaign", "opinion", "bulsageum_trend", "seogeumwon"]),
    ("2026-07-28", "lowcredit_report", ["yoon_ad_bill"]),
    ("2026-07-02", "fsc_decree", ["geumsoyeon_edu", "arrest_case", "roundup"]),
])
def test_original_pollution_controls_stay_separate(date, event, others) -> None:
    rows, cids = real_clusters(date)
    event_cards = {cid for row, cid in zip(rows, cids) if row["event"] == event}
    assert not [row["title"] for row, cid in zip(rows, cids) if row["event"] in others and cid in event_cards]
