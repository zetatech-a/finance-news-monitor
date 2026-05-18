from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts import refresh_relevance_candidate_model as refresh
from src.ml.relevance_model import load_model, predict_proba


def _write_candidate_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date", "title", "summary", "url", "query", "score", "prob", "keep", "decision",
        "decision_reason", "relevance_score", "relevance_prob", "matched_hard", "matched_soft",
        "matched_negative",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{column: row.get(column, "") for column in fieldnames} for row in rows])


def _candidate_rows(date: str, pos: int = 6, neg: int = 6, prefix: str = "hist") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i in range(pos):
        rows.append({
            "date": date,
            "title": f"은행 가계대출 연체 관리 강화 {prefix} {i}",
            "summary": "금융권 리스크와 감독 정책 점검",
            "url": f"https://example.test/{prefix}/p/{i}",
            "query": "은행 가계대출",
            "decision": "keep",
            "decision_reason": "rule_keep_positive_score",
            "relevance_score": "8",
            "matched_hard": "은행;가계대출",
        })
    for i in range(neg):
        rows.append({
            "date": date,
            "title": f"항공사 환율 유가 영업이익 감소 {prefix} {i}",
            "summary": "원자재 가격과 매출 실적 관련 산업 기사",
            "url": f"https://example.test/{prefix}/n/{i}",
            "query": "환율",
            "decision": "drop",
            "decision_reason": "rule_drop_no_financial_anchor",
            "relevance_score": "1",
        })
    return rows


def _run_refresh(tmp_path: Path, extra_args: list[str] | None = None) -> tuple[int, Path, Path, Path, Path, Path]:
    candidates_dir = tmp_path / "reports" / "_candidates"
    _write_candidate_csv(candidates_dir / "2026-05-11_candidates.csv", _candidate_rows("2026-05-11", prefix="d1"))
    _write_candidate_csv(candidates_dir / "2026-05-12_candidates.csv", _candidate_rows("2026-05-12", prefix="d2"))
    model_path = tmp_path / "models" / "relevance_candidate.joblib"
    metrics_path = tmp_path / "reports" / "_metrics" / "2026-05-13_relevance_candidate_eval.json"
    report_path = tmp_path / "reports" / "_metrics" / "2026-05-13_relevance_candidate_eval.txt"
    disagreements_path = tmp_path / "reports" / "_metrics" / "2026-05-13_relevance_disagreements.csv"
    status_path = tmp_path / "reports" / "_metrics" / "2026-05-13_candidate_model_refresh.json"
    args = [
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(model_path),
        "--metrics-output", str(metrics_path),
        "--report-output", str(report_path),
        "--disagreements-output", str(disagreements_path),
        "--status-output", str(status_path),
        "--report-date", "2026-05-13",
        "--min-positive", "2",
        "--min-negative", "2",
        "--seed", "42",
        "--force",
    ]
    if extra_args:
        args.extend(extra_args)
    return refresh.main(args), model_path, metrics_path, report_path, disagreements_path, status_path


def test_refresh_creates_candidate_model_from_historical_candidate_csvs(tmp_path):
    code, model_path, metrics_path, report_path, disagreements_path, _ = _run_refresh(tmp_path)

    assert code == 0
    assert model_path.exists()
    assert metrics_path.exists()
    assert report_path.exists()
    assert disagreements_path.exists()


def test_refresh_excludes_current_report_date_candidate_csv(tmp_path):
    candidates_dir = tmp_path / "reports" / "_candidates"
    _write_candidate_csv(candidates_dir / "2026-05-11_candidates.csv", _candidate_rows("2026-05-11", prefix="hist"))
    _write_candidate_csv(candidates_dir / "2026-05-13_candidates.csv", _candidate_rows("2026-05-13", pos=20, neg=0, prefix="current"))
    model_path = tmp_path / "models" / "relevance_candidate.joblib"
    status_path = tmp_path / "reports" / "_metrics" / "status.json"
    metrics_path = tmp_path / "reports" / "_metrics" / "metrics.json"

    code = refresh.main([
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(model_path),
        "--metrics-output", str(metrics_path),
        "--report-output", str(tmp_path / "reports" / "_metrics" / "report.txt"),
        "--disagreements-output", str(tmp_path / "reports" / "_metrics" / "disagreements.csv"),
        "--status-output", str(status_path),
        "--report-date", "2026-05-13",
        "--min-positive", "2",
        "--min-negative", "2",
        "--min-candidate-files", "1",
        "--force",
    ])

    assert code == 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert any(item["path"] == str(candidates_dir / "2026-05-13_candidates.csv") for item in status["excluded_candidate_files"])
    assert str(candidates_dir / "2026-05-13_candidates.csv") not in status["input_candidate_files"]
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert str(candidates_dir / "2026-05-13_candidates.csv") not in metrics["input_files"]


def test_refresh_writes_refreshed_status_on_success(tmp_path):
    code, model_path, _, _, _, status_path = _run_refresh(tmp_path)

    assert code == 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "refreshed"
    assert status["model_output"] == str(model_path)
    assert status["model_exists_before"] is False
    assert status["model_exists_after"] is True
    assert status["pseudo_label_rows"] > 0
    assert status["trainable_positive"] >= 2
    assert status["trainable_negative"] >= 2


def test_best_effort_returns_zero_and_writes_skipped_status_when_training_data_insufficient(tmp_path):
    candidates_dir = tmp_path / "reports" / "_candidates"
    _write_candidate_csv(candidates_dir / "2026-05-11_candidates.csv", _candidate_rows("2026-05-11", pos=2, neg=0, prefix="only_pos"))
    status_path = tmp_path / "reports" / "_metrics" / "status.json"

    code = refresh.main([
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(tmp_path / "models" / "relevance_candidate.joblib"),
        "--metrics-output", str(tmp_path / "reports" / "_metrics" / "metrics.json"),
        "--report-output", str(tmp_path / "reports" / "_metrics" / "report.txt"),
        "--disagreements-output", str(tmp_path / "reports" / "_metrics" / "disagreements.csv"),
        "--status-output", str(status_path),
        "--min-positive", "2",
        "--min-negative", "2",
        "--min-candidate-files", "1",
        "--force",
        "--best-effort",
    ])

    assert code == 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "skipped"
    assert "both positive and negative" in status["error"]


def test_strict_mode_returns_nonzero_when_training_data_insufficient(tmp_path):
    candidates_dir = tmp_path / "reports" / "_candidates"
    _write_candidate_csv(candidates_dir / "2026-05-11_candidates.csv", _candidate_rows("2026-05-11", pos=1, neg=0, prefix="only_pos"))
    status_path = tmp_path / "reports" / "_metrics" / "status.json"

    code = refresh.main([
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(tmp_path / "models" / "relevance_candidate.joblib"),
        "--metrics-output", str(tmp_path / "reports" / "_metrics" / "metrics.json"),
        "--report-output", str(tmp_path / "reports" / "_metrics" / "report.txt"),
        "--disagreements-output", str(tmp_path / "reports" / "_metrics" / "disagreements.csv"),
        "--status-output", str(status_path),
        "--min-positive", "2",
        "--min-negative", "2",
        "--min-candidate-files", "1",
        "--force",
    ])

    assert code == 2
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "skipped"


def test_refresh_refuses_operating_model_output_and_does_not_create_it(tmp_path):
    status_path = tmp_path / "reports" / "_metrics" / "status.json"
    operating_model = Path("models/relevance.joblib")
    existed_before = operating_model.exists()

    code = refresh.main([
        "--candidates-dir", str(tmp_path / "reports" / "_candidates"),
        "--model-output", "models/relevance.joblib",
        "--metrics-output", str(tmp_path / "reports" / "_metrics" / "metrics.json"),
        "--report-output", str(tmp_path / "reports" / "_metrics" / "report.txt"),
        "--disagreements-output", str(tmp_path / "reports" / "_metrics" / "disagreements.csv"),
        "--status-output", str(status_path),
        "--force",
    ])

    assert code == 2
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "failed"
    assert operating_model.exists() is existed_before


def test_generated_candidate_model_loads_and_predicts_with_relevance_helpers(tmp_path):
    code, model_path, *_ = _run_refresh(tmp_path)
    assert code == 0

    model = load_model(model_path)
    probs = predict_proba(model, ["은행\n은행\n가계대출 연체", "항공\n항공\n환율 영업이익"])

    assert len(probs) == 2
    assert all(0.0 <= prob <= 1.0 for prob in probs)


def test_workflow_contains_refresh_step_before_daily_report_with_best_effort_and_dated_metrics():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    refresh_index = workflow.index("- name: Refresh candidate relevance model")
    run_daily_index = workflow.index("- name: Run daily report")
    assert refresh_index < run_daily_index
    block = workflow[refresh_index:run_daily_index]
    assert "if: ${{ steps.phase5_recheck.outputs.should_run == 'true' }}" in block
    assert "python scripts/refresh_relevance_candidate_model.py" in block
    assert "--best-effort" in block
    assert 'reports/_metrics/${report_date}_candidate_model_refresh.json' in block
    assert 'reports/_metrics/${report_date}_relevance_candidate_eval.json' in block
    assert 'reports/_metrics/${report_date}_relevance_candidate_eval.txt' in block
    assert 'reports/_metrics/${report_date}_relevance_disagreements.csv' in block


def test_workflow_preserves_phase5_marker_send_flow():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    for step in (
        "Phase 5 marker precheck",
        "Wait until target send window",
        "Phase 5 marker recheck",
        "Email precheck",
        "Send email",
        "Write sent marker",
        "Commit reports and sent marker",
    ):
        assert f"- name: {step}" in workflow
    assert "python scripts/phase5_delivery.py mark-sent" in workflow
    assert "git add reports/" in workflow
    assert "git add models/" not in workflow
