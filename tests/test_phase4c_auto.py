from __future__ import annotations

import csv
import json
from pathlib import Path

from src.pipeline import relevance_filter as rf
from src.run_daily import choose_relevance_model_policy


def _article(title: str, description: str = "") -> dict[str, str]:
    return {
        "title": title,
        "description": description,
        "link": f"https://example.com/{abs(hash(title))}",
    }


def _use_model(monkeypatch, probs: list[float]) -> None:
    monkeypatch.setattr(rf, "load_model", lambda path: object())
    monkeypatch.setattr(rf, "predict_proba", lambda model, texts: probs)


def test_candidate_hybrid_negative_signal_drops_even_with_high_prob(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.99])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 8)
    articles = [_article("은행 프로야구")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
    )

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_negative_signal"


def test_candidate_hybrid_strong_rule_anchor_keeps_even_with_low_prob(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.01])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 9)
    articles = [_article("은행 가계대출 연체")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
    )

    assert kept == articles
    assert articles[0]["decision_reason"] == "candidate_hybrid_keep_strong_domain_rule_anchor"


def test_candidate_hybrid_high_prob_needs_domain_evidence(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.80])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 1)
    articles = [_article("모호한 경제 기사")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
        candidate_keep_prob=0.65,
    )

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_model_keep_without_domain_anchor"


def test_candidate_hybrid_low_prob_drops_ambiguous_article(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.20])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 5)
    articles = [_article("모호한 경제 기사")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
        candidate_drop_prob=0.35,
    )

    assert kept == []
    assert articles[0]["decision_reason"] == "candidate_hybrid_model_drop_prob_le_threshold"


def test_candidate_hybrid_gray_zone_requires_domain_anchor_and_higher_score(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.50, 0.50])
    scores = iter([6, 3])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [_article("저축은행 연체율 상승"), _article("은행 소식")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
        candidate_keep_prob=0.65,
        candidate_drop_prob=0.35,
    )

    assert kept == [articles[0]]
    assert articles[0]["decision_reason"] == "candidate_hybrid_gray_keep_domain_score_ge_threshold"
    assert articles[1]["decision_reason"] == "candidate_hybrid_gray_drop_score_lt_threshold"


def test_authoritative_model_still_drops_low_prob_even_with_high_rule_score(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.10])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 10)
    articles = [_article("은행 가계대출 연체")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance.joblib",
        model_policy="authoritative",
        min_prob=0.55,
    )

    assert kept == []
    assert articles[0]["decision_reason"] == "model_drop_prob_lt_threshold"


def test_rule_only_ignores_model_probability_and_uses_score(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.99, 0.01])
    scores = iter([3, 4])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [_article("은행 소식 1"), _article("은행 소식 2")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="rule_only",
        min_score=4,
    )

    assert kept == [articles[1]]
    assert articles[0]["prob"] is None
    assert articles[0]["decision_reason"] == "rule_drop_score_lt_threshold"
    assert articles[1]["decision_reason"] == "rule_keep_score_ge_threshold"



def test_invalid_candidate_model_falls_back_to_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rf,
        "load_model",
        lambda path: (_ for _ in ()).throw(ValueError("bad model")),
    )
    scores = iter([4, 3])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    articles = [_article("저축은행 대출"), _article("은행 대출")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
    )

    assert kept == []
    assert articles[0]["prob"] is None
    assert articles[0]["model_used"] is False
    assert articles[0]["decision_reason"] == "candidate_hybrid_no_model_drop_score_lt_threshold"
    assert articles[1]["decision_reason"] == "candidate_hybrid_no_model_drop_score_lt_threshold"


def test_candidate_csv_contains_phase4c_observability_columns(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.75])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 2)
    out = tmp_path / "candidates.csv"

    rf.filter_relevance(
        [_article("모호한 금융 기사")],
        tmp_path / "relevance_candidate.joblib",
        out_candidates_csv=out,
        model_policy="candidate_hybrid",
    )

    with out.open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["relevance_model_policy"] == "candidate_hybrid"
    assert row["model_used"] == "1"
    assert row["candidate_keep_prob"] == "0.65"
    assert row["candidate_drop_prob"] == "0.35"


def test_relevance_filter_metrics_json_is_written(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.80, 0.20])
    scores = iter([5, 2])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    metrics_path = tmp_path / "reports" / "_metrics" / "2026-05-13_relevance_filter_metrics.json"

    kept = rf.filter_relevance(
        [_article("저축은행 연체율 상승"), _article("후보 drop")],
        tmp_path / "models" / "relevance_candidate.joblib",
        model_policy="candidate_hybrid",
        metrics_path=metrics_path,
        metrics_date="2026-05-13",
    )

    assert len(kept) == 1
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-05-13"
    assert payload["model_policy"] == "candidate_hybrid"
    assert payload["input_count"] == 2
    assert payload["kept_count"] == 1
    assert "decision_reason_counts" in payload
    assert payload["candidate_hybrid_model_keep"] == 1
    assert payload["candidate_hybrid_model_drop"] == 1


def test_metrics_write_failure_does_not_break_filtering(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "relevance_score", lambda article: 4)
    monkeypatch.setattr(rf, "_write_metrics", lambda **kwargs: (_ for _ in ()).throw(OSError("boom")))
    articles = [_article("은행 대출")]

    kept = rf.filter_relevance(
        articles,
        tmp_path / "missing.joblib",
        model_policy="rule_only",
        metrics_path=tmp_path / "metrics.json",
    )

    assert kept == articles


def test_choose_relevance_model_policy_prefers_operating_model(tmp_path):
    operating = tmp_path / "models" / "relevance.joblib"
    candidate = tmp_path / "models" / "relevance_candidate.joblib"
    candidate.parent.mkdir()
    operating.write_text("operating", encoding="utf-8")
    candidate.write_text("candidate", encoding="utf-8")

    assert choose_relevance_model_policy(
        operating_model_path=operating,
        candidate_model_path=candidate,
    ) == (operating, "authoritative")


def test_choose_relevance_model_policy_uses_candidate_when_no_operating_model(tmp_path):
    operating = tmp_path / "models" / "relevance.joblib"
    candidate = tmp_path / "models" / "relevance_candidate.joblib"
    candidate.parent.mkdir()
    candidate.write_text("candidate", encoding="utf-8")

    assert choose_relevance_model_policy(
        operating_model_path=operating,
        candidate_model_path=candidate,
    ) == (candidate, "candidate_hybrid")
    assert not operating.exists()


def test_choose_relevance_model_policy_uses_rule_only_when_no_model_exists(tmp_path):
    operating = tmp_path / "models" / "relevance.joblib"
    candidate = tmp_path / "models" / "relevance_candidate.joblib"

    assert choose_relevance_model_policy(
        operating_model_path=operating,
        candidate_model_path=candidate,
    ) == (operating, "rule_only")
    assert not operating.exists()


def test_disable_candidate_model_prevents_candidate_hybrid(tmp_path):
    operating = tmp_path / "models" / "relevance.joblib"
    candidate = tmp_path / "models" / "relevance_candidate.joblib"
    candidate.parent.mkdir()
    candidate.write_text("candidate", encoding="utf-8")

    assert choose_relevance_model_policy(
        operating_model_path=operating,
        candidate_model_path=candidate,
        disable_candidate_model=True,
    ) == (operating, "rule_only")
    assert not operating.exists()
