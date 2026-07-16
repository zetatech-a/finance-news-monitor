from __future__ import annotations

from pathlib import Path

import joblib


def model_input_text(title: str, summary: str) -> str:
    """관련성 모델(운영/후보 공통)의 입력 텍스트.

    제목을 2회 반복해 TF-IDF에서 제목 토큰에 가중치를 준다.
    학습 스크립트(train_relevance*, refresh)와 운영 추론(relevance_filter)이
    반드시 이 함수를 공유해야 한다 — 형식이 어긋나면 학습/서빙 불일치로
    평가 지표와 운영 확률이 어긋난다.
    """
    title = (title or "").strip()
    summary = (summary or "").strip()
    return f"{title}\n{title}\n{summary}".strip()


def load_model(path: Path):
    if not path.exists():
        return None
    return joblib.load(path)


def predict_proba(model, texts: list[str]) -> list[float]:
    if not hasattr(model, "predict_proba"):
        raise TypeError("relevance model must provide predict_proba(texts)")

    probabilities = model.predict_proba(texts)
    classes = list(getattr(model, "classes_", []))
    if not classes and hasattr(model, "named_steps"):
        classifier = model.named_steps.get("clf")
        classes = list(getattr(classifier, "classes_", []))
    if 1 not in classes:
        raise ValueError("relevance model predict_proba output does not include class 1")
    positive_index = classes.index(1)
    return [float(row[positive_index]) for row in probabilities]
