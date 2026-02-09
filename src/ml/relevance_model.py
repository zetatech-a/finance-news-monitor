from __future__ import annotations

from pathlib import Path
import joblib

def load_model(path: Path):
    if not path.exists():
        return None
    return joblib.load(path)

def predict_proba(model, texts: list[str]) -> list[float]:
    # model은 sklearn pipeline (vectorizer+classifier) 형태
    proba = model.predict_proba(texts)[:, 1]
    return [float(p) for p in proba]
