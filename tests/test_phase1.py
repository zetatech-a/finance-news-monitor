from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import yaml

from src.pipeline import relevance_filter as rf
from src.pipeline.normalize import Article
from src.pipeline.report import render_html
from src.pipeline.tagger import TaggedArticle, tag_articles


def _article(title: str = "기사", description: str = "내용") -> dict[str, str]:
    return {"title": title, "description": description, "link": "https://example.com/a"}


def test_no_model_rule_score_below_four_drops(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "relevance_score", lambda article: 3)

    kept = rf.filter_relevance([_article("금감원 검사")], tmp_path / "missing.joblib")

    assert kept == []


def test_no_model_rule_score_at_least_four_keeps(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "relevance_score", lambda article: 4)

    articles = [_article("금감원 검사")]
    kept = rf.filter_relevance(articles, tmp_path / "missing.joblib")

    assert kept == articles
    assert articles[0]["decision"] == "keep"
    assert articles[0]["decision_reason"] == "rule_keep_score_ge_threshold"


def test_low_model_probability_drops_even_with_high_rule_score(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "load_model", lambda path: object())
    monkeypatch.setattr(rf, "predict_proba", lambda model, texts: [0.10])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 10)

    articles = [_article("금감원 제재 금융회사")]
    kept = rf.filter_relevance(articles, tmp_path / "model.joblib", min_prob=0.55)

    assert kept == []
    assert articles[0]["decision"] == "drop"
    assert articles[0]["decision_reason"] == "model_drop_prob_lt_threshold"


def test_model_probability_at_least_threshold_keeps(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "load_model", lambda path: object())
    monkeypatch.setattr(rf, "predict_proba", lambda model, texts: [0.60])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 0)

    articles = [_article("잡기사")]
    kept = rf.filter_relevance(articles, tmp_path / "model.joblib", min_prob=0.55)

    assert kept == articles
    assert articles[0]["decision"] == "keep"
    assert articles[0]["decision_reason"] == "model_keep_prob_ge_threshold"


def test_candidate_csv_contains_phase1_observability_columns(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "relevance_score", lambda article: 4)
    out = tmp_path / "candidates.csv"

    rf.filter_relevance([_article("금감원 검사 은행")], tmp_path / "missing.joblib", out_candidates_csv=out)

    with out.open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    for column in (
        "score",
        "prob",
        "keep",
        "decision",
        "decision_reason",
        "relevance_score",
        "relevance_prob",
        "matched_hard",
        "matched_soft",
        "matched_negative",
    ):
        assert column in row
    assert row["decision"] == "keep"


def test_standalone_broad_fetch_queries_are_absent():
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))

    assert not {"환율", "회사채", "저축은행", "금융위", "금감원"}.intersection(
        data["fetch_queries"]
    )


def _report_article(title: str, *, score=None, prob=None) -> Article:
    article = Article(
        title=title,
        description="요약",
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 11, 9, 0),
        query="test",
    )
    if score is not None:
        article.relevance_score = score
    if prob is not None:
        article.relevance_prob = prob
    return article


def test_low_confidence_misc_articles_are_not_rendered_in_html_report():
    low_misc = TaggedArticle(_report_article("저신뢰 기타 기사", score=2, prob=0.20), ["기타"], [], [])
    blank_misc = TaggedArticle(_report_article("빈 확률 기타 기사", score=2), ["기타"], [], [])
    high_misc = TaggedArticle(_report_article("고신뢰 기타 기사", score=7), ["기타"], [], [])

    html = render_html(datetime(2026, 5, 11), [low_misc, blank_misc, high_misc], [])

    assert "저신뢰 기타 기사" not in html
    assert "빈 확률 기타 기사" not in html
    assert "고신뢰 기타 기사" in html


def test_low_confidence_misc_articles_are_not_rendered_in_top_issues():
    low_misc = TaggedArticle(_report_article("저신뢰 기타 톱 후보", score=99, prob=0.20), ["기타"], [], [])
    bank_item = TaggedArticle(_report_article("은행권 주요 기사", score=4), ["은행"], [], [])

    html = render_html(datetime(2026, 5, 11), [low_misc, bank_item], [])
    top_start = html.index('id="sec-TOP"')
    top_end = html.index("</section>", top_start)
    top_section = html[top_start:top_end]

    assert "저신뢰 기타 톱 후보" not in top_section
    assert "은행권 주요 기사" in top_section


def _tag_one(title: str, description: str = "") -> TaggedArticle:
    data = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description=description,
        link=f"https://example.com/{title}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 11, 9, 0),
        query="test",
    )
    return tag_articles([article], data["sectors"], data["topics"])[0]


def test_corporate_earnings_with_generic_macro_terms_is_not_primary_macro():
    tagged = _tag_one("제주항공, 고환율·유가 부담에 1분기 영업이익 감소")

    assert tagged.sectors[0] != "거시·시장"


def test_real_market_article_remains_macro_market():
    tagged = _tag_one("원달러 환율, 외환시장 변동성 확대")

    assert tagged.sectors[0] == "거시·시장"


def test_financial_company_article_prefers_financial_sector_over_macro():
    tagged = _tag_one("카카오뱅크, 고금리에도 1분기 순이익 증가")

    assert tagged.sectors[0] == "은행"


def test_regulator_with_enforcement_action_classifies_as_supervisory():
    tagged = _tag_one("금감원, 은행권 대출 검사 착수")

    assert tagged.sectors[0] == "감독·제재"


def test_regulator_with_policy_context_prefers_policy_not_supervisory():
    tagged = _tag_one("금융위, 금융권 제도개선 방안 발표")

    assert tagged.sectors[0] == "입법·정책"
    assert tagged.sectors[0] != "감독·제재"


def test_standalone_regulator_mention_is_not_primary_supervisory():
    tagged = _tag_one("금감원장, 금융권 간담회 참석")

    assert tagged.sectors[0] != "감독·제재"


def test_standalone_generic_macro_terms_do_not_force_primary_macro():
    for title in ("환율", "금리", "유가", "달러"):
        tagged = _tag_one(title)

        assert tagged.sectors[0] != "거시·시장"


def test_securities_article_prefers_financial_sector_over_generic_macro():
    tagged = _tag_one("증권사 금리 상승에 채권 평가손실")

    assert tagged.sectors[0] == "증권(브로커리지/리테일)"


def test_supervisory_action_without_regulator_anchor_is_not_primary_supervisory():
    tagged = _tag_one("검사 착수")

    assert tagged.sectors[0] != "감독·제재"


def test_top_issue_rank_weight_matches_composite_topic_labels():
    from src.pipeline.report import _top_rank_score

    composite_topic = TaggedArticle(
        _report_article("복합 토픽 기사"), ["은행"], ["부동산·PF"], []
    )
    unweighted_topic = TaggedArticle(
        _report_article("일반 토픽 기사"), ["은행"], ["일반"], []
    )

    assert _top_rank_score(composite_topic) > _top_rank_score(unweighted_topic)
