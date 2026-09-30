"""Compact 불법대부 compounds stay lending anchors; only the 대부도 continuation is excluded."""
from __future__ import annotations

import pytest

from scripts.evaluate_loan_news import make_article
from src.pipeline import relevance_filter as rf
from src.pipeline.relevance_score import relevance_score
from src.pipeline.text_matcher import contains_term, find_terms


def decide(title: str, prob: float = 0.8) -> tuple[int, list[str], bool, bool]:
    article = make_article({"title": title, "summary": "", "url": "https://example.test/" + title})
    article.relevance_prob = prob
    score = relevance_score(article)
    matched = rf._matched_terms(article)
    keep, _ = rf._decide_relevance(
        score=score, prob=prob, min_score=4, min_prob=0.55, model_policy="candidate_hybrid",
        article_text=f"{article.title}\n{article.description}",
        matched_hard=matched["matched_hard"], matched_negative=matched["matched_negative"],
    )
    return score, matched["matched_hard"].split(";"), rf.has_domain_anchor(article), keep


@pytest.mark.parametrize("title", [
    "불법대부광고 단속", "불법대부행위 근절", "불법대부계약 피해", "불법대부일당 검거",
    "불법대부 특별단속", "불법대부업 기승", "불법대부업체 적발", "불법대부중개업 적발", "불법대부를 단속",
])
def test_compact_lending_compounds_keep_anchor_on_both_paths(title: str) -> None:
    score, matched_hard, anchor, keep = decide(title)
    assert contains_term(title, "불법대부") and find_terms(title, ["불법대부"]) == ["불법대부"]
    assert "불법대부" in matched_hard
    assert rf.has_domain_anchor(title) and anchor
    assert score >= 6 and keep


@pytest.mark.parametrize("title", ["불법대부도 토지거래", "불법 대부도 토지거래"])
def test_daebudo_continuation_is_still_not_an_anchor(title: str) -> None:
    score, matched_hard, anchor, keep = decide(title)
    assert not contains_term(title, "불법대부")
    assert "불법대부" not in matched_hard
    assert not rf.has_domain_anchor(title) and not anchor and not keep


def test_daebudo_exclusion_does_not_remove_independent_mentions() -> None:
    title = "불법대부도 토지거래 논란 속 불법대부업체 적발"
    score, matched_hard, anchor, keep = decide(title)
    assert contains_term(title, "불법대부") and "불법대부" in matched_hard
    assert anchor and keep
