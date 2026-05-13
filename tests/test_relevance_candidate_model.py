from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from scripts import train_relevance_candidate_model as train
from src.ml.relevance_model import load_model, predict_proba


def _write_csv(path, rows, fieldnames=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = [
            "date", "title", "summary", "url", "auto_label", "auto_label_confidence", "auto_label_reason",
            "train_weight", "excluded_from_training", "decision", "decision_reason", "relevance_score",
            "matched_hard", "matched_soft", "matched_negative",
        ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _rows(pos=6, neg=6, review=2):
    rows = []
    for i in range(pos):
        rows.append({
            "date": "2026-05-11", "title": f"은행 가계대출 연체 관리 {i}", "summary": "금융권 리스크와 감독 정책",
            "url": f"https://e/p{i}", "auto_label": "1", "auto_label_confidence": "0.95", "auto_label_reason": "strong_keep_bank_consumer_credit",
            "train_weight": "0.95", "excluded_from_training": "false", "decision": "keep", "relevance_score": "8", "matched_hard": "은행",
        })
    for i in range(neg):
        rows.append({
            "date": "2026-05-11", "title": f"항공사 환율 영업이익 감소 {i}", "summary": "원자재 유가 실적 관련 산업 기사",
            "url": f"https://e/n{i}", "auto_label": "0", "auto_label_confidence": "0.85", "auto_label_reason": "drop_corporate_macro_noise",
            "train_weight": "0.85", "excluded_from_training": "false", "decision": "drop", "relevance_score": "1",
        })
    for i in range(review):
        rows.append({
            "date": "2026-05-11", "title": f"금리 시장 관망 {i}", "summary": "회사채 환율 맥락 모호",
            "url": f"https://e/r{i}", "auto_label": "review", "auto_label_confidence": "0.00", "auto_label_reason": "review_ambiguous_market_context",
            "train_weight": "0.00", "excluded_from_training": "true", "decision": "drop", "relevance_score": "4",
        })
    return rows


def _run_train(tmp_path, rows=None, extra_args=None):
    input_path = tmp_path / "labels.csv"
    model_path = tmp_path / "models" / "relevance_candidate.joblib"
    metrics_path = tmp_path / "metrics.json"
    report_path = tmp_path / "report.txt"
    disagreements_path = tmp_path / "disagreements.csv"
    _write_csv(input_path, rows if rows is not None else _rows())
    args = [
        "--input", str(input_path), "--model-output", str(model_path), "--metrics-output", str(metrics_path),
        "--report-output", str(report_path), "--disagreements-output", str(disagreements_path),
        "--min-positive", "2", "--min-negative", "2", "--seed", "42", "--force",
    ]
    if extra_args:
        args.extend(extra_args)
    code = train.main(args)
    return code, model_path, metrics_path, report_path, disagreements_path


def test_training_script_is_runnable_from_repo_root():
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/train_relevance_candidate_model.py", "--help"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Train/evaluate a candidate relevance model" in result.stdout


def test_training_writes_candidate_model_metrics_and_report(tmp_path):
    code, model_path, metrics_path, report_path, disagreements_path = _run_train(tmp_path)

    assert code == 0
    assert model_path.exists()
    assert metrics_path.exists()
    assert report_path.exists()
    assert disagreements_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["used_rows"] == 12
    assert payload["ignored_rows"]["review"] == 2


def test_saved_candidate_model_compatible_with_relevance_model_helpers(tmp_path):
    code, model_path, *_ = _run_train(tmp_path)
    assert code == 0

    model = load_model(model_path)
    probs = predict_proba(model, ["은행\n은행\n가계대출 연체", "항공\n항공\n환율 영업이익"])

    assert len(probs) == 2
    assert all(0.0 <= prob <= 1.0 for prob in probs)


def test_review_rows_are_ignored_during_training(tmp_path):
    code, _, metrics_path, _, _ = _run_train(tmp_path, _rows(pos=2, neg=2, review=3), ["--min-positive", "1", "--min-negative", "1"])

    assert code == 0
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["used_rows"] == 4
    assert payload["label_counts"]["review"] == 3


def test_invalid_auto_label_fails_clearly(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(input_path, [{"title": "기사", "summary": "요약", "auto_label": "yes"}])

    code = train.main(["--input", str(input_path), "--model-output", str(tmp_path / "m.joblib"), "--min-positive", "1", "--min-negative", "1"])

    assert code == 2


def test_missing_required_columns_fail_clearly(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(input_path, [{"title": "기사", "auto_label": "1"}], fieldnames=["title", "auto_label"])

    code = train.main(["--input", str(input_path), "--model-output", str(tmp_path / "m.joblib"), "--min-positive", "1", "--min-negative", "1"])

    assert code == 2


def test_single_class_training_data_fails_clearly(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(input_path, _rows(pos=3, neg=0, review=0))

    code = train.main(["--input", str(input_path), "--model-output", str(tmp_path / "m.joblib"), "--min-positive", "1", "--min-negative", "1"])

    assert code == 2


def test_existing_model_output_is_not_overwritten_without_force(tmp_path):
    input_path = tmp_path / "labels.csv"
    model_path = tmp_path / "model.joblib"
    _write_csv(input_path, _rows(pos=2, neg=2, review=0))
    model_path.write_text("existing", encoding="utf-8")

    code = train.main(["--input", str(input_path), "--model-output", str(model_path), "--min-positive", "1", "--min-negative", "1"])

    assert code == 2
    assert model_path.read_text(encoding="utf-8") == "existing"


def test_small_dataset_trains_and_marks_evaluation_skipped(tmp_path):
    code, model_path, metrics_path, _, _ = _run_train(tmp_path, _rows(pos=1, neg=1, review=1), ["--min-positive", "1", "--min-negative", "1"])

    assert code == 0
    assert model_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["evaluation_skipped"] is True
    assert payload["evaluation_skip_reason"]


def test_metrics_json_contains_required_sections(tmp_path):
    code, _, metrics_path, _, _ = _run_train(tmp_path)
    assert code == 0

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))

    assert "label_counts" in payload
    assert "split" in payload
    assert "threshold_metrics" in payload
    assert "evaluation_skipped" in payload
    assert len(payload["threshold_metrics"]) == 6


def test_disagreement_csv_has_expected_columns(tmp_path):
    code, _, _, _, disagreements_path = _run_train(tmp_path)
    assert code == 0

    with disagreements_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == train.DISAGREEMENT_COLUMNS
        list(reader)


def test_operating_model_output_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "labels.csv"
    _write_csv(input_path, _rows(pos=2, neg=2, review=0))

    code = train.main(["--input", str(input_path), "--model-output", "models/relevance.joblib", "--min-positive", "1", "--min-negative", "1", "--force"])

    assert code == 2
    assert not (tmp_path / "models" / "relevance.joblib").exists()
