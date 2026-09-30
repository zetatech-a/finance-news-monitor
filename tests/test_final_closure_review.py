"""Final bounded closure: plural metric subjects, compact 불법대부 and legacy police names."""
from __future__ import annotations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import issue_cluster as ic
from src.pipeline import relevance_filter as rf
from src.pipeline.normalize import Article
from src.pipeline.relevance_score import relevance_score
from src.pipeline.tagger import TaggedArticle
from src.pipeline.text_matcher import contains_term


def item(title: str, sector: str = "보험") -> TaggedArticle:
    return TaggedArticle(make_article({"title": title, "summary": "",
                                      "url": "https://example.test/" + title}), [sector], [], [])


def pair(a: TaggedArticle, b: TaggedArticle, merge: bool) -> None:
    assert ic._should_cluster(a, b) is merge
    assert len(ic.cluster_tagged_articles([a, b])) == (1 if merge else 2)


def event_text(title: str) -> str:
    return ic._metric_event_text(ic._build_cluster_features(item(title)))


# F2: a recognized plural industry subject is removed with its plural marker.
def test_plural_subject_does_not_count_as_shared_event_evidence() -> None:
    a = item("보험사들 2분기 킥스비율 200% 대규모 자사주 매입 추진 방안 이사회 의결")
    b = item("보험사들 상반기 지급여력비율 200% 후순위채 조기 상환")
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.metric_subjects == fb.metric_subjects == {"보험사"}
    assert "보험사들" not in ic._metric_event_tokens(fa) | ic._metric_event_tokens(fb)
    assert ic._metric_match_lacks_event_evidence(fa, fb)
    pair(a, b, False)


def test_plural_subject_same_event_still_merges() -> None:
    pair(item("보험사들 2분기 킥스비율 200% 자사주 매입 결정"),
         item("보험사들 상반기 지급여력비율 200% 자사주 매입 결정"), True)


@pytest.mark.parametrize("title,kept", [
    ("보험사 2분기 킥스비율 200% 고객들 이탈", "고객들"),                # arbitrary noun ending in 들
    ("보험사 2분기 킥스비율 200% 보험사들로부터 이탈", "보험사들로부터"),  # lexical continuation
    ("보험업계들 2분기 킥스비율 200% 하락", "보험업계들"),              # unknown industry phrase
    ("삼성생명 2분기 킥스비율 200% 보험사들 평균 상회", "보험사들"),     # post-measurement phrase
])
def test_plural_removal_stays_bounded(title: str, kept: str) -> None:
    assert kept in event_text(title)


def test_plural_subject_identity_contracts_unchanged() -> None:
    f = {t: ic._build_cluster_features(item(t)) for t in (
        "보험회사들 2분기 킥스비율 200% 하락", "생보사들 2분기 킥스비율 200% 하락",
        "손보사들 2분기 킥스비율 200% 하락", "삼성생명 2분기 킥스비율 200% 하락")}
    subjects = [feature.metric_subjects for feature in f.values()]
    assert subjects == [{"보험사"}, {"생보사"}, {"손보사"}, {"삼성생명"}]
    values = list(f.values())
    assert all(ic._conflicting_metric_subjects(values[0], other) for other in values[1:])


# F3: the compact spelling uses the same continuation boundary as its spaced alias.
def article(title: str, prob: float) -> Article:
    value = make_article({"title": title, "summary": "", "url": "https://example.test/" + title})
    value.relevance_prob = prob
    return value


def decide(value: Article) -> tuple[int, str, bool, bool]:
    score = relevance_score(value)
    matched = rf._matched_terms(value)
    keep, _ = rf._decide_relevance(
        score=score, prob=value.relevance_prob, min_score=4, min_prob=0.55,
        model_policy="candidate_hybrid", article_text=f"{value.title}\n{value.description}",
        matched_hard=matched["matched_hard"], matched_negative=matched["matched_negative"],
    )
    return score, matched["matched_hard"], rf.has_domain_anchor(value), keep


@pytest.mark.parametrize("prob", [0.5, 0.8])
@pytest.mark.parametrize("title", ["불법대부도 토지거래", "불법 대부도 토지거래"])
def test_daebudo_continuation_is_not_a_lending_anchor(title: str, prob: float) -> None:
    score, matched_hard, anchor, keep = decide(article(title, prob))
    assert "불법대부" not in matched_hard.split(";")
    assert not contains_term(title, "불법대부")
    assert not rf.has_domain_anchor(title)
    assert not anchor and not keep
    assert score < 4


@pytest.mark.parametrize("title", [
    "불법대부 특별단속", "불법대부업 기승", "불법대부업체 적발", "불법대부를 근절",
    "불법 대부업체 적발", "불법대부중개업 적발",
])
def test_legitimate_compact_and_spaced_lending_forms_remain_anchors(title: str) -> None:
    score, matched_hard, anchor, keep = decide(article(title, 0.8))
    assert "불법대부" in matched_hard.split(";")
    assert contains_term(title, "불법대부") and rf.has_domain_anchor(title)
    assert anchor and keep and score >= 6


# F4: only former metropolitan 지방경찰청 names map to the current agency.
@pytest.mark.parametrize("raw,canonical", [
    ("부산지방경찰청", "부산경찰청"), ("서울지방경찰청", "서울경찰청"),
    ("서울시경찰청", "서울경찰청"), ("서울특별시경찰청", "서울경찰청"),
    ("부산시", "부산시"), ("부산광역시", "부산시"),
    ("경기지방경찰청", "경기지방경찰청"), ("충북지방경찰청", "충북지방경찰청"),
])
def test_bounded_legacy_police_canonicalization(raw: str, canonical: str) -> None:
    assert ic._canonical_local_authority(raw) == canonical


def test_legacy_metropolitan_police_wires_share_fingerprint() -> None:
    a = item("부산지방경찰청 청소년 불법사금융 특별 단속", "대부")
    b = item("부산경찰청 청소년 불법대부 합동 수사", "대부")
    fa, fb = map(ic._build_cluster_features, (a, b))
    assert fa.fingerprint == fb.fingerprint == "enforcement:부산경찰청:youth"
    pair(a, b, True)


@pytest.mark.parametrize("other", [
    "대구경찰청 청소년 불법대부 합동 수사",   # different city police agency
    "부산시 청소년 불법대부 합동 수사",       # municipal government, not police
])
def test_legacy_police_alias_does_not_merge_other_authorities(other: str) -> None:
    a = item("부산지방경찰청 청소년 불법사금융 특별 단속", "대부")
    fa, fb = map(ic._build_cluster_features, (a, item(other, "대부")))
    assert fa.fingerprint != fb.fingerprint
    pair(a, item(other, "대부"), False)
