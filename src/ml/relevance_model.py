from __future__ import annotations

from pathlib import Path

import joblib


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
