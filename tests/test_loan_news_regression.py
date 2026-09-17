from __future__ import annotations

from itertools import permutations

import pytest
from bs4 import BeautifulSoup

from scripts.evaluate_loan_news import FIXTURE, load_rows, make_article, replay
from src.pipeline.issue_cluster import _should_cluster, cluster_tagged_articles
from src.pipeline.issue_cluster import _reported_metrics
from src.pipeline.filtering import filter_articles
from src.pipeline.report import render_html, visible_report_items
from src.pipeline.tagger import TaggedArticle


def test_september_17_golden_events_survive_without_pollution():
    rows = load_rows(FIXTURE)
    tagged, reps = replay(rows)
    labels = {row["url"]: row for row in rows}
    assert {item.article.link for item in tagged} == {row["url"] for row in rows if row["relevant"]}
    clusters: dict[str, set[str]] = {}
    for item in tagged:
        clusters.setdefault(item.article.cluster_id, set()).add(labels[item.article.link]["event"])
    assert all(len(events) == 1 for events in clusters.values())
    # Same-event recall as well as purity: splitting everything is not a fix.
    for event in ("fund_purchase", "seoul_crackdown", "insurance_capital", "pg_security"):
        ids = {item.article.cluster_id for item in tagged if labels[item.article.link]["event"] == event}
        assert len(ids) == 1, (event, ids)
    visible = visible_report_items(reps)
    for event in ("fund_purchase", "civil_policy", "loan_supply", "credit_access"):
        assert any(labels[item.article.link]["event"] == event and item.sectors == ["대부"] for item in visible)
    html = render_html(tagged[0].article.pub_date, reps, [])
    cards = BeautifulSoup(html, "html.parser").select("article[data-card][data-sector='대부'] [data-title]")
    titles = {card.get_text() for card in cards}
    assert any(title.startswith("새도약기금") for title in titles)
    assert any(title.startswith("한국TI") for title in titles)


def _tag(title: str, summary: str = "") -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": summary, "url": f"https://example.com/{title}"}), ["대부"], [], [])


def test_different_illegal_lending_events_are_not_a_shared_fingerprint():
    items = [
        _tag("부산경찰, 불법사금융 조직 12명 검거"),
        _tag("시민단체 불법사금융 피해지원 제도 개선안 제출"),
        _tag("내구제대출 피해자 통신요금 구제 신청 개시"),
    ]
    assert len(cluster_tagged_articles(items)) == 3


def test_local_enforcement_requires_same_authority_and_target():
    items = [
        _tag("부산시 전통시장 불법사금융 집중 단속"),
        _tag("인천시 전통시장 불법사금융 집중 단속"),
        _tag("부산시 청소년 불법사금융 집중 단속"),
        _tag("부산시 소상공인 불법사금융 수사 확대"),
    ]
    reps = cluster_tagged_articles(items)
    assert len(reps) == 3
    assert items[0].article.cluster_id == items[3].article.cluster_id
    assert len({item.article.cluster_id for item in items[:3]}) == 3


def test_metric_anchor_binds_value_to_measurement_not_shared_change():
    assert _reported_metrics("보험사 킥스비율 215.2% 0.8%p 하락") == {("capital_adequacy_ratio", "215.2")}
    assert not _reported_metrics("보험사 킥스비율 0.8%p 하락")
    assert _reported_metrics("보험사 킥스비율 215.2%") != _reported_metrics("보험사 킥스비율 203.6%")


def test_background_fund_reference_does_not_identify_policy_event():
    fund = _tag("새도약기금 대부업권 채권 매입 협상 진행")
    policy = _tag("시민단체 불법사금융 피해지원 제도 개선안 제출", "새도약기금 대부업권 채권 매입 현황도 참고했다.")
    assert not _should_cluster(fund, policy)


def test_positive_golden_candidates_also_pass_prefilter():
    rows = [row for row in load_rows(FIXTURE) if row["relevant"]]
    articles = [make_article(row) for row in rows]
    assert filter_articles(articles) == articles


def test_bridge_cannot_merge_incompatible_endpoints_in_any_input_order():
    a = _tag("가온대부 채권매각 협상중단")
    b = _tag("가온대부 채권매각 협상중단 나래대부 전산장애 피해보상")
    c = _tag("나래대부 전산장애 피해보상")
    assert _should_cluster(a, b) and _should_cluster(b, c)
    assert not _should_cluster(a, c)
    memberships = []
    for order in permutations([a, b, c]):
        cluster_tagged_articles(list(order))
        assert a.article.cluster_id != c.article.cluster_id
        memberships.append(tuple(item.article.cluster_id for item in (a, b, c)))
    assert len(set(memberships)) == 1


@pytest.mark.parametrize("title", [
    "공유재산 대부계약 체결", "국유재산 대부료 인상", "대부도 여행 인기",
    "힙합의 대부 가수 콘서트", "영화 대부 재개봉", "대부분 지역 축제 개최",
])
def test_broad_loan_homonym_noise_still_drops(title):
    # Synthetic noise guards, not samples from an unavailable Naver search.
    tagged, _ = replay([{"title": title, "summary": "", "url": "https://example.com/noise", "prob": 0.8}])
    assert not tagged
