from __future__ import annotations

import csv
import json

from src.pipeline import relevance_filter as rf


def _article(title: str, description: str = "") -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "link": f"https://example.com/{abs(hash(title))}",
    }


def _use_model(monkeypatch, probs: list[float]) -> None:
    monkeypatch.setattr(rf, "load_model", lambda path: object())
    monkeypatch.setattr(rf, "predict_proba", lambda model, texts: probs)


def test_phase8c_keeps_high_value_domain_articles_with_low_or_gray_prob(monkeypatch, tmp_path):
    titles = [
        "저축은행 연체율 상승",
        "신협 연체율 상승",
        "새마을금고 PF 부실 우려",
        "카드론 연체율 상승",
        "여신금융협회 카드수수료 논의",
        "보험사 킥스 비율 하락",
        "금감원 은행권 대출 검사 착수",
        "금융위 대부업 제도개선 방안 발표",
        "불법사금융 특별단속",
    ]
    _use_model(monkeypatch, [0.20, 0.50, 0.20, 0.50, 0.20, 0.20, 0.20, 0.20, 0.20])
    articles = [_article(title) for title in titles]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
    )

    assert kept == articles
    assert all(article["decision"] == "keep" for article in articles)
    assert {article["decision_reason"] for article in articles} <= {
        "candidate_hybrid_keep_strong_domain_rule_anchor",
        "candidate_hybrid_gray_keep_domain_score_ge_threshold",
    }


def test_phase8c_drops_weak_generic_or_nonfinance_cases(monkeypatch, tmp_path):
    titles = [
        "항공사 고환율·유가 부담에 영업이익 감소",
        "AI 기업 IPO 상장 흥행",
        "미국 CPI 상승에 뉴욕증시 혼조",
        "연준 금리 발언",
        "한국거래소 코스닥 상장 심사",
        "건강보험 재정 악화",
        "운송·보험료 상승에 항공사 실적 악화",
        "프로야구 감독 경질",
    ]
    _use_model(monkeypatch, [0.80, 0.80, 0.80, 0.50, 0.50, 0.80, 0.80, 0.99])
    articles = [_article(title) for title in titles]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
    )

    assert kept == [articles[2]]
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_corporate_macro_noise"
    assert articles[1]["decision_reason"] == "candidate_hybrid_drop_generic_market_or_ipo_noise"
    assert articles[2]["decision_reason"] in {
        "candidate_hybrid_keep_overseas_global_reference",
        "candidate_hybrid_model_keep_prob_ge_threshold_with_domain_anchor",
        "candidate_hybrid_keep_strong_domain_rule_anchor",
    }
    assert articles[3]["decision_reason"] == "candidate_hybrid_drop_generic_market_or_ipo_noise"
    assert articles[4]["decision_reason"] == "candidate_hybrid_drop_generic_market_or_ipo_noise"
    assert articles[5]["decision_reason"] == "candidate_hybrid_drop_model_keep_without_domain_anchor"
    assert articles[6]["decision_reason"] == "candidate_hybrid_drop_corporate_macro_noise"
    assert articles[7]["decision_reason"] == "candidate_hybrid_drop_negative_signal"


def test_phase8c_negative_signal_wins_over_high_model_prob(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.99])
    articles = [_article("금감원 은행 검사 프로야구 감독 경질")]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_negative_signal"


def test_phase8c_strong_generic_anchor_does_not_keep_low_prob(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.20])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 9)
    articles = [_article("연준 금리 CPI 국채 시장")]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_generic_market_or_ipo_noise"


def test_phase8c_high_model_prob_without_domain_anchor_does_not_keep(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.90])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 2)
    articles = [_article("모호한 경제 기사")]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_model_keep_without_domain_anchor"


def test_phase8c_gray_zone_requires_domain_anchor_and_threshold(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.50, 0.50, 0.50])
    scores = iter([6, 6, 5])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [
        _article("저축은행 연체율 상승"),
        _article("금리 환율 동향"),
        _article("신협 연체율 상승"),
    ]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == [articles[0]]
    assert articles[0]["decision_reason"] == "candidate_hybrid_gray_keep_domain_score_ge_threshold"
    assert articles[1]["decision_reason"] == "candidate_hybrid_gray_drop_generic_anchor_only"
    assert articles[2]["decision_reason"] == "candidate_hybrid_gray_drop_score_lt_threshold"


def test_phase8c_no_prob_fallback_requires_domain_anchor_and_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "load_model", lambda path: (_ for _ in ()).throw(ValueError("bad")))
    scores = iter([5, 5, 4])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [
        _article("저축은행 연체율 상승"),
        _article("금리 환율 동향"),
        _article("신협 연체율 상승"),
    ]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == [articles[0]]
    assert articles[0]["decision_reason"] == "candidate_hybrid_no_model_keep_domain_score_ge_threshold"
    assert articles[1]["decision_reason"] == "candidate_hybrid_no_model_drop_no_domain_anchor"
    assert articles[2]["decision_reason"] == "candidate_hybrid_no_model_drop_score_lt_threshold"


def test_phase8c_authoritative_and_rule_only_semantics_unchanged(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.10])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 10)
    authoritative_article = _article("금감원 은행권 대출 검사 착수")

    kept = rf.filter_relevance(
        [authoritative_article],
        tmp_path / "relevance.joblib",
        model_policy="authoritative",
        min_prob=0.55,
    )

    assert kept == []
    assert authoritative_article["decision_reason"] == "model_drop_prob_lt_threshold"

    scores = iter([3, 4])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [_article("은행 소식 1"), _article("은행 소식 2")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "missing.joblib",
        model_policy="rule_only",
        min_score=4,
    )

    assert kept == [articles[1]]
    assert articles[0]["decision_reason"] == "rule_drop_score_lt_threshold"
    assert articles[1]["decision_reason"] == "rule_keep_score_ge_threshold"


def test_phase8c_metrics_and_candidate_csv_are_backward_compatible(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.20, 0.50, 0.80])
    articles = [
        _article("금감원 은행권 대출 검사 착수"),
        _article("AI 기업 IPO 상장 흥행"),
        _article("모호한 경제 기사"),
    ]
    out = tmp_path / "candidates.csv"
    metrics_path = tmp_path / "metrics.json"

    kept = rf.filter_relevance(
        articles,
        tmp_path / "candidate.joblib",
        out_candidates_csv=out,
        model_policy="candidate_hybrid",
        metrics_path=metrics_path,
        metrics_date="2026-05-18",
    )

    assert kept == [articles[0]]
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["decision_reason_counts"]["candidate_hybrid_keep_strong_domain_rule_anchor"] == 1
    assert payload["decision_reason_counts"]["candidate_hybrid_drop_generic_market_or_ipo_noise"] == 1
    assert payload["decision_reason_counts"]["candidate_hybrid_drop_model_keep_without_domain_anchor"] == 1
    assert payload["candidate_hybrid_keep_strong_domain_rule_anchor"] == 1
    assert payload["candidate_hybrid_drop_generic_market_or_ipo_noise"] == 1

    with out.open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    for column in [
        "title",
        "summary",
        "url",
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
        "relevance_model_policy",
        "model_used",
        "candidate_keep_prob",
        "candidate_drop_prob",
    ]:
        assert column in row
