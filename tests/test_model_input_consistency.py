from __future__ import annotations

from pathlib import Path

from scripts.train_relevance_candidate_model import _text as candidate_training_text
from scripts.train_relevance import load_labels
from src.ml.relevance_model import model_input_text
from src.pipeline import relevance_filter


def test_candidate_training_text_matches_runtime_model_input():
    row = {"title": "대부업 연체율 급등", "summary": "요약문입니다"}
    assert candidate_training_text(row) == model_input_text(row["title"], row["summary"])


def test_operating_model_training_text_matches_runtime_model_input(tmp_path):
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "title,summary,label\n대부업 연체율 급등,요약문입니다,1\n", encoding="utf-8"
    )

    texts, _ = load_labels(labels)

    assert texts == [model_input_text("대부업 연체율 급등", "요약문입니다")]


def test_filter_relevance_feeds_model_doubled_title(monkeypatch, tmp_path):
    """운영 추론이 학습과 같은 형식(제목 2회 가중)을 모델에 넣는지 검증."""
    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(relevance_filter, "load_model", lambda path: object())

    def fake_predict(model, texts):
        captured["texts"] = list(texts)
        return [0.9 for _ in texts]

    monkeypatch.setattr(relevance_filter, "predict_proba", fake_predict)

    articles = [{"title": "대부업 연체율 급등", "summary": "요약문입니다"}]
    kept = relevance_filter.filter_relevance(
        articles,
        model_path=Path(tmp_path / "model.joblib"),
        model_policy="authoritative",
        min_prob=0.55,
    )

    assert captured["texts"] == ["대부업 연체율 급등\n대부업 연체율 급등\n요약문입니다"]
    assert len(kept) == 1
