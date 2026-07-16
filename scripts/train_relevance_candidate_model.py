#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from src.ml.relevance_model import model_input_text, predict_proba

REQUIRED_COLUMNS = {"title", "summary", "auto_label"}
VALID_LABELS = {"1", "0", "review", ""}
THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
DISAGREEMENT_COLUMNS = [
    "date", "title", "summary", "url", "auto_label", "auto_label_confidence", "auto_label_reason",
    "model_prob", "model_pred", "disagreement_type", "decision", "decision_reason", "relevance_score",
    "matched_hard", "matched_soft", "matched_negative",
]


def _text(row: dict[str, str]) -> str:
    # 운영 추론(relevance_filter)과 동일한 형식이어야 한다 — 공용 함수 사용
    return model_input_text(row.get("title", ""), row.get("summary", ""))


def _weight(row: dict[str, str]) -> float:
    raw = (row.get("train_weight") or row.get("auto_label_confidence") or "1").strip()
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value > 0 else 1.0


def load_rows(paths: Sequence[Path]) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    warnings: list[str] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"{path}: missing required columns: {', '.join(sorted(missing))}")
            for raw in reader:
                row = {key: (value or "") for key, value in raw.items()}
                label = (row.get("auto_label") or "").strip()
                if label not in VALID_LABELS:
                    raise ValueError(f"{path}: invalid auto_label value {label!r}; expected 1, 0, or review")
                rows.append(row)
    if not rows:
        warnings.append("input files contained no rows")
    return rows, warnings


def usable_rows(rows: Iterable[dict[str, str]]) -> tuple[list[dict[str, str]], dict[str, int]]:
    used: list[dict[str, str]] = []
    ignored = {"review": 0, "blank_or_invalid": 0}
    for row in rows:
        label = (row.get("auto_label") or "").strip()
        if label == "review":
            ignored["review"] += 1
            continue
        if label not in {"1", "0"} or not _text(row):
            ignored["blank_or_invalid"] += 1
            continue
        used.append(row)
    return used, ignored


def build_model(seed: int) -> Pipeline:
    # 운영 모델(train_relevance.py)과 동일한 char 2-5gram — 한국어는 형태소 분석
    # 없이도 char ngram이 강력하고, 복합어 경계('국민은행' 속 '은행')도 자연히 잡는다.
    return Pipeline([
        ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=1, max_features=50000)),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)),
    ])


def _fit_model(rows: list[dict[str, str]], seed: int) -> Pipeline:
    model = build_model(seed)
    texts = [_text(row) for row in rows]
    labels = [int(row["auto_label"]) for row in rows]
    weights = [_weight(row) for row in rows]
    model.fit(texts, labels, clf__sample_weight=weights)
    return model


def _confusion(y_true: Sequence[int], probs: Sequence[float], threshold: float) -> dict[str, float | int]:
    preds = [1 if p >= threshold else 0 for p in probs]
    tp = sum(1 for y, p in zip(y_true, preds) if y == 1 and p == 1)
    tn = sum(1 for y, p in zip(y_true, preds) if y == 0 and p == 0)
    fp = sum(1 for y, p in zip(y_true, preds) if y == 0 and p == 1)
    fn = sum(1 for y, p in zip(y_true, preds) if y == 1 and p == 0)
    total = len(y_true)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    accuracy = (tp + tn) / total if total else 0.0
    return {
        "threshold": threshold,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
    }


def evaluate(rows: list[dict[str, str]], seed: int, test_size: float, threshold: float) -> tuple[dict[str, object], list[dict[str, str]], list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    labels = [int(row["auto_label"]) for row in rows]
    pos = sum(labels)
    neg = len(labels) - pos
    min_class = min(pos, neg)
    if len(rows) < 4 or min_class < 2:
        reason = "holdout split requires at least 2 rows per class and 4 usable rows"
        return ({
            "split": {"train_rows": len(rows), "test_rows": 0, "test_size": test_size, "seed": seed, "stratified": False},
            "metrics": None,
            "threshold_metrics": [],
            "evaluation_skipped": True,
            "evaluation_skip_reason": reason,
        }, rows, [], warnings)

    stratified = True
    try:
        train_rows, test_rows = train_test_split(rows, test_size=test_size, random_state=seed, stratify=labels)
    except ValueError as exc:
        warnings.append(f"stratified split unavailable: {exc}")
        train_rows, test_rows = train_test_split(rows, test_size=test_size, random_state=seed, stratify=None)
        stratified = False
    if not train_rows or not test_rows or len({row["auto_label"] for row in train_rows}) < 2 or len({row["auto_label"] for row in test_rows}) < 2:
        reason = "holdout split did not preserve both classes in train and test sets"
        return ({
            "split": {"train_rows": len(rows), "test_rows": 0, "test_size": test_size, "seed": seed, "stratified": stratified},
            "metrics": None,
            "threshold_metrics": [],
            "evaluation_skipped": True,
            "evaluation_skip_reason": reason,
        }, rows, [], warnings)

    eval_model = _fit_model(train_rows, seed)
    y_test = [int(row["auto_label"]) for row in test_rows]
    probs = predict_proba(eval_model, [_text(row) for row in test_rows])
    threshold_metrics = [_confusion(y_test, probs, t) for t in THRESHOLDS]
    metrics = _confusion(y_test, probs, threshold)
    return ({
        "split": {"train_rows": len(train_rows), "test_rows": len(test_rows), "test_size": test_size, "seed": seed, "stratified": stratified},
        "metrics": metrics,
        "threshold_metrics": threshold_metrics,
        "evaluation_skipped": False,
        "evaluation_skip_reason": "",
    }, train_rows, test_rows, warnings)


def write_json(path: Path | None, payload: dict[str, object]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report(path: Path | None, payload: dict[str, object]) -> None:
    if not path:
        return
    label_counts = payload["label_counts"]
    split = payload["split"]
    metrics = payload.get("metrics")
    lines = [
        "Relevance candidate model evaluation",
        f"Input files: {', '.join(payload['input_files'])}",
        f"Total rows: {payload['total_rows']}",
        f"Used rows: {payload['used_rows']}",
        f"Labels: positive={label_counts['positive']} negative={label_counts['negative']} review={label_counts['review']}",
        f"Split: train={split['train_rows']} test={split['test_rows']} stratified={split['stratified']}",
        f"Evaluation skipped: {payload['evaluation_skipped']}",
    ]
    if payload.get("evaluation_skipped"):
        lines.append(f"Skip reason: {payload['evaluation_skip_reason']}")
    elif isinstance(metrics, dict):
        lines.append(
            "Metrics @ {threshold:.2f}: accuracy={accuracy:.3f} precision={precision:.3f} recall={recall:.3f} f1={f1:.3f}".format(**metrics)
        )
    lines.append(f"Model output: {payload['model_output']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _severity(row: dict[str, str], threshold: float) -> tuple[float, float]:
    try:
        confidence = float(row.get("auto_label_confidence") or 0)
        prob = float(row.get("model_prob") or 0)
    except ValueError:
        return (0, 0)
    return (confidence, abs(prob - threshold))


def write_disagreements(path: Path | None, model: Pipeline, rows: list[dict[str, str]], threshold: float, max_rows: int) -> None:
    if not path:
        return
    output: list[dict[str, str]] = []
    texts = [_text(row) for row in rows]
    probs = predict_proba(model, texts) if texts else []
    for row, prob in zip(rows, probs):
        label = (row.get("auto_label") or "").strip()
        pred = 1 if prob >= threshold else 0
        disagreement_type = ""
        if label in {"1", "0"} and pred != int(label):
            disagreement_type = "pseudo_keep_model_drop" if label == "1" else "pseudo_drop_model_keep"
        elif label == "review" and prob >= 0.85:
            disagreement_type = "review_model_strong_keep"
        elif label == "review" and prob <= 0.15:
            disagreement_type = "review_model_strong_drop"
        if not disagreement_type:
            continue
        out = {column: row.get(column, "") for column in DISAGREEMENT_COLUMNS}
        out.update({"model_prob": f"{prob:.6f}", "model_pred": str(pred), "disagreement_type": disagreement_type})
        output.append(out)
    output.sort(key=lambda r: _severity(r, threshold), reverse=True)
    output = output[:max_rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DISAGREEMENT_COLUMNS)
        writer.writeheader()
        writer.writerows(output)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train/evaluate a candidate relevance model from automatic pseudo-labels.")
    parser.add_argument("--input", dest="inputs", action="append", type=Path, required=True)
    parser.add_argument("--model-output", type=Path, default=Path("models/relevance_candidate.joblib"))
    parser.add_argument("--metrics-output", type=Path, default=Path("reports/_metrics/relevance_candidate_eval.json"))
    parser.add_argument("--report-output", type=Path, default=Path("reports/_metrics/relevance_candidate_eval.txt"))
    parser.add_argument("--disagreements-output", type=Path, default=Path("reports/_metrics/relevance_disagreements.csv"))
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-positive", type=int, default=50)
    parser.add_argument("--min-negative", type=int, default=50)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--max-disagreements", type=int, default=5000)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.model_output.resolve() == Path("models/relevance.joblib").resolve():
            raise ValueError("refusing to write operating model path models/relevance.joblib; use models/relevance_candidate.joblib")
        if args.model_output.exists() and not args.force:
            raise FileExistsError(f"Model output already exists: {args.model_output}. Use --force to overwrite.")
        rows, warnings = load_rows(args.inputs)
        used, ignored = usable_rows(rows)
        labels = [(row.get("auto_label") or "").strip() for row in rows]
        pos = sum(1 for row in used if row["auto_label"] == "1")
        neg = sum(1 for row in used if row["auto_label"] == "0")
        review = sum(1 for label in labels if label == "review")
        if not used:
            raise ValueError("no usable rows remain after excluding review/blank labels")
        if pos == 0 or neg == 0:
            raise ValueError("training data must contain both positive and negative classes")
        if pos < args.min_positive:
            raise ValueError(f"positive count {pos} is below --min-positive {args.min_positive}")
        if neg < args.min_negative:
            raise ValueError(f"negative count {neg} is below --min-negative {args.min_negative}")

        eval_payload, _, _, eval_warnings = evaluate(used, args.seed, args.test_size, args.threshold)
        warnings.extend(eval_warnings)
        model = _fit_model(used, args.seed)
        args.model_output.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, args.model_output)

        payload: dict[str, object] = {
            "input_files": [str(path) for path in args.inputs],
            "total_rows": len(rows),
            "used_rows": len(used),
            "ignored_rows": ignored,
            "label_counts": {"positive": pos, "negative": neg, "review": review},
            "warnings": warnings,
            "model_output": str(args.model_output),
            **eval_payload,
        }
        write_json(args.metrics_output, payload)
        write_report(args.report_output, payload)
        write_disagreements(args.disagreements_output, model, rows, args.threshold, args.max_disagreements)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"input_files={len(args.inputs)} total_rows={len(rows)} used_rows={len(used)} review_rows={review} "
        f"positive={pos} negative={neg} model_output={args.model_output} metrics_output={args.metrics_output} "
        f"disagreements_output={args.disagreements_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
