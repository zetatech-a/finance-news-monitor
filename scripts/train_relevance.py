from __future__ import annotations

import csv
import sys
from pathlib import Path

import joblib
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.ml.relevance_model import model_input_text

def load_labels(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if not row.get("label"):
                continue
            rows.append(row)
    # 운영 추론(relevance_filter)과 동일한 입력 형식이어야 한다 — 공용 함수 사용
    texts = [model_input_text(row.get("title", ""), row.get("summary", "")) for row in rows]
    y = [int(row["label"]) for row in rows]
    return texts, y

def main():
    labels_path = Path("data/relevance_labels.csv")
    if not labels_path.exists():
        raise FileNotFoundError(labels_path)

    X, y = load_labels(labels_path)
    if len(y) < 40:
        raise RuntimeError(f"Need at least ~40 labeled rows. Current: {len(y)}")

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    pipe = Pipeline([
        # 한국어는 형태소 분석 없이도 char ngram이 강력함
        ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(2,5), min_df=2)),
        ("clf", LogisticRegression(max_iter=2000)),
    ])

    pipe.fit(X_train, y_train)
    pred = pipe.predict(X_test)

    print(classification_report(y_test, pred, digits=3))

    out = Path("models/relevance.joblib")
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, out)
    print(f"Saved model: {out}")

if __name__ == "__main__":
    main()
