from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import yaml

from src.pipeline import relevance_filter as rf
from src.pipeline.normalize import Article
from src.pipeline.tagger import tag_articles


def _article(title: str, description: str = "") -> dict[str, str]:
    return {"title": title, "description": description, "link": f"https://example.com/{abs(hash(title))}"}


def _use_model(monkeypatch, probs: list[float]) -> None:
    monkeypatch.setattr(rf, "load_model", lambda path: object())
    monkeypatch.setattr(rf, "predict_proba", lambda model, texts: probs)


def _topic_for(title: str) -> set[str]:
    cfg = yaml.safe_load(Path("queries.yml").read_text(encoding="utf-8"))
    article = Article(
        title=title,
        description="",
        link=f"https://e/{abs(hash(title))}",
        originallink=None,
        naver_link=None,
        pub_date=datetime(2026, 5, 19, 9, 0),
        query="phase8c2",
    )
    tagged = tag_articles([article], cfg["sectors"], cfg["topics"])[0]
    return set(tagged.topics)


def test_phase8c2_keeps_domain_and_overseas_reference_cases(monkeypatch, tmp_path):
    keep_titles = [
        # A. Domestic finance impact overseas
        "미 국채금리 급등에 국내 채권시장 변동성 확대",
        "연준 긴축 우려에 원달러 환율 상승, 은행권 대출금리 압박",
        "달러 강세에 외환시장 불안…한국은행 대응 주목",
        "글로벌 채권금리 상승에 국고채 금리 급등",
        "FOMC 이후 국내 금융시장 변동성 확대",
        # B. Important overseas/global macro reference
        "미국 CPI 상승에 뉴욕증시 혼조",
        "FOMC 앞두고 미 증시 변동성 확대",
        "미 국채금리 급등에 뉴욕증시 하락",
        "미국 PCE 발표 앞두고 국채금리 상승",
        "글로벌 신용위험 확산에 은행주 약세",
        # D. Existing Phase 8C-2 recall
        "하나금융, 두나무와 원화 스테이블코인 사업 검토",
        "FIU, 가상자산거래소 제재 착수",
        "금융위, 증권사 유동성비율 규제 전체 증권사로 확대",
        "신조정유동성비율 도입에 증권사 ABCP 관리 강화",
        "카드채 만기 몰리는데 여전채 금리 4% 돌파",
        "산업은행 국민성장펀드 조성 본격화",
    ]
    _use_model(monkeypatch, [0.5] * len(keep_titles))
    monkeypatch.setattr(rf, "relevance_score", lambda article: 8)
    articles = [_article(t) for t in keep_titles]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert len(kept) == len(keep_titles)
    assert all(a["decision"] == "keep" for a in articles)
    assert all(
        a["decision_reason"] not in {
            "candidate_hybrid_drop_generic_market_or_ipo_noise",
            "candidate_hybrid_drop_corporate_macro_noise",
            "candidate_hybrid_drop_model_keep_without_domain_anchor",
            "candidate_hybrid_drop_low_value_overseas_market_noise",
        }
        for a in articles
    )


def test_phase8c2_drops_low_value_overseas_and_existing_noise(monkeypatch, tmp_path):
    titles = [
        # C. low-value overseas noise
        "AI 기술주 랠리에 월가 환호",
        "테슬라 주가 급등",
        "엔비디아 주가 신고가",
        "뉴욕증시 상승 마감",
        "나스닥 상승 마감",
        "월가 전문가가 꼽은 유망 종목",
        "미국 기업 실적 호조에 주가 상승",
        # E. existing noise
        "항공사 고환율·유가 부담에 영업이익 감소",
        "제조업체 원자재 가격 상승에 실적 악화",
        "운송·보험료 상승에 항공사 실적 악화",
        "AI 기업 IPO 상장 흥행",
    ]
    _use_model(monkeypatch, [0.9] * len(titles))
    articles = [_article(t) for t in titles]

    kept = rf.filter_relevance(articles, tmp_path / "candidate.joblib", model_policy="candidate_hybrid")

    assert kept == []
    assert all(a["decision"] == "drop" for a in articles)
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_low_value_overseas_market_noise"
    assert articles[3]["decision_reason"] == "candidate_hybrid_drop_low_value_overseas_market_noise"
    assert articles[7]["decision_reason"] == "candidate_hybrid_drop_corporate_macro_noise"
    assert articles[10]["decision_reason"] == "candidate_hybrid_drop_generic_market_or_ipo_noise"


def test_phase8c2_policy_invariants_and_metrics_csv(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.95, 0.95, 0.1, 0.99])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 2)
    articles = [
        _article("모호한 경제 기사"),
        _article("미국 CPI 상승에 뉴욕증시 혼조"),
        _article("산업은행 국민성장펀드 조성 본격화"),
        _article("금감원 은행 검사 프로야구 감독 경질"),
    ]
    out_csv = tmp_path / "candidate.csv"
    metrics = tmp_path / "metrics.json"

    kept = rf.filter_relevance(
        articles,
        tmp_path / "candidate.joblib",
        out_candidates_csv=out_csv,
        metrics_path=metrics,
        model_policy="candidate_hybrid",
    )

    assert kept == [articles[1]]
    assert articles[0]["decision_reason"] == "candidate_hybrid_drop_model_keep_without_domain_anchor"
    assert articles[3]["decision_reason"] == "candidate_hybrid_drop_negative_signal"

    payload = json.loads(metrics.read_text(encoding="utf-8"))
    assert "decision_reason_counts" in payload
    assert payload["decision_reason_counts"]["candidate_hybrid_drop_model_keep_without_domain_anchor"] == 1
    assert payload["decision_reason_counts"]["candidate_hybrid_drop_negative_signal"] == 1

    with out_csv.open(encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    for col in ("decision_reason", "relevance_model_policy", "candidate_keep_prob", "candidate_drop_prob"):
        assert col in row


def test_phase8c2_authoritative_and_rule_only_unchanged(monkeypatch, tmp_path):
    _use_model(monkeypatch, [0.1])
    monkeypatch.setattr(rf, "relevance_score", lambda article: 10)
    a = _article("금감원 은행권 대출 검사 착수")
    kept = rf.filter_relevance([a], tmp_path / "relevance.joblib", model_policy="authoritative", min_prob=0.55)
    assert kept == []
    assert a["decision_reason"] == "model_drop_prob_lt_threshold"

    scores = iter([3, 4])
    monkeypatch.setattr(rf, "relevance_score", lambda article: next(scores))
    arr = [_article("은행 소식 1"), _article("은행 소식 2")]
    kept_rule = rf.filter_relevance(arr, tmp_path / "missing.joblib", model_policy="rule_only", min_score=4)
    assert kept_rule == [arr[1]]
    assert arr[0]["decision_reason"] == "rule_drop_score_lt_threshold"
    assert arr[1]["decision_reason"] == "rule_keep_score_ge_threshold"


def test_phase8c2_overseas_reference_gets_global_topic():
    assert "해외·글로벌" in _topic_for("미국 CPI 상승에 뉴욕증시 혼조")
