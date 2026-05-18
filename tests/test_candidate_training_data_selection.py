from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts import refresh_relevance_candidate_model as refresh


BASE_COLUMNS = ["date", "title", "summary", "url"]
PHASE1_COLUMNS = BASE_COLUMNS + [
    "query", "score", "prob", "keep", "decision", "decision_reason", "relevance_score", "relevance_prob",
    "matched_hard", "matched_soft", "matched_negative",
]
PHASE4C_COLUMNS = PHASE1_COLUMNS + ["relevance_model_policy", "model_used", "candidate_keep_prob", "candidate_drop_prob"]


def _write_csv(path: Path, rows: list[dict[str, str]], columns: list[str] = PHASE1_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{column: row.get(column, "") for column in columns} for row in rows])


def _rows(date: str, prefix: str, pos: int = 3, neg: int = 3) -> list[dict[str, str]]:
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
            "score": "8",
            "prob": "0.91",
            "relevance_score": "8",
            "relevance_prob": "0.91",
            "matched_hard": "은행;가계대출",
            "matched_soft": "연체",
            "matched_negative": "",
            "relevance_model_policy": "candidate_hybrid",
            "model_used": "candidate",
            "candidate_keep_prob": "0.88",
            "candidate_drop_prob": "0.12",
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
            "score": "1",
            "prob": "0.05",
            "relevance_score": "1",
            "relevance_prob": "0.05",
            "matched_hard": "",
            "matched_soft": "",
            "matched_negative": "",
            "relevance_model_policy": "candidate_hybrid",
            "model_used": "candidate",
            "candidate_keep_prob": "0.08",
            "candidate_drop_prob": "0.92",
        })
    return rows


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    candidates_dir = tmp_path / "reports" / "_candidates"
    status = tmp_path / "reports" / "_metrics" / "status.json"
    model = tmp_path / "models" / "relevance_candidate.joblib"
    return candidates_dir, status, model


def _run(tmp_path: Path, candidates_dir: Path, status: Path, model: Path, extra: list[str] | None = None) -> int:
    args = [
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(model),
        "--metrics-output", str(tmp_path / "reports" / "_metrics" / "metrics.json"),
        "--report-output", str(tmp_path / "reports" / "_metrics" / "report.txt"),
        "--disagreements-output", str(tmp_path / "reports" / "_metrics" / "disagreements.csv"),
        "--status-output", str(status),
        "--report-date", "2026-05-18",
        "--min-positive", "1",
        "--min-negative", "1",
        "--force",
    ]
    if extra:
        args.extend(extra)
    return refresh.main(args)


def _status(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _excluded(status: dict[str, object], reason: str) -> list[dict[str, object]]:
    return [item for item in status["excluded_candidate_files"] if item["reason"] == reason]


def test_date_filters_exclude_current_future_and_outside_lookback(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    for date, prefix in [("2026-05-17", "eligible"), ("2026-05-18", "current"), ("2026-05-19", "future"), ("2026-04-26", "old")]:
        _write_csv(candidates_dir / f"{date}_candidates.csv", _rows(date, prefix))

    assert _run(tmp_path, candidates_dir, status_path, model, ["--best-effort", "--min-candidate-files", "1"]) == 0

    status = _status(status_path)
    assert status["input_candidate_files"] == [str(candidates_dir / "2026-05-17_candidates.csv")]
    assert _excluded(status, "current_report_date")
    assert _excluded(status, "future_report_date")
    assert _excluded(status, "outside_lookback_window")


def test_uses_only_most_recent_eligible_files_up_to_max(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    for day in range(10, 18):
        date = f"2026-05-{day:02d}"
        _write_csv(candidates_dir / f"{date}_candidates.csv", _rows(date, f"d{day}"))

    assert _run(tmp_path, candidates_dir, status_path, model, ["--max-candidate-files", "3"]) == 0

    status = _status(status_path)
    assert status["input_candidate_files"] == [
        str(candidates_dir / "2026-05-17_candidates.csv"),
        str(candidates_dir / "2026-05-16_candidates.csv"),
        str(candidates_dir / "2026-05-15_candidates.csv"),
    ]
    assert status["excluded_reason_counts"]["beyond_max_candidate_files"] == 5


def test_phase1_debug_schema_requires_decision_debug_and_matched_columns(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "bad"), columns=BASE_COLUMNS)

    assert _run(tmp_path, candidates_dir, status_path, model, ["--best-effort", "--min-candidate-files", "1"]) == 0

    missing = _excluded(_status(status_path), "missing_required_columns")[0]["missing_columns"]
    assert "decision" in missing
    assert "decision_reason" in missing
    assert "matched_hard" in missing
    assert "relevance_score or score" in missing


def test_phase4c_hybrid_schema_requires_policy_and_threshold_columns(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "p1"), columns=PHASE1_COLUMNS)

    assert _run(tmp_path, candidates_dir, status_path, model, ["--best-effort", "--min-candidate-files", "1", "--min-candidate-schema", "phase4c_hybrid"]) == 0

    missing = _excluded(_status(status_path), "missing_required_columns")[0]["missing_columns"]
    assert {"relevance_model_policy", "model_used", "candidate_keep_prob", "candidate_drop_prob"}.issubset(set(missing))


def test_min_candidate_schema_any_accepts_minimal_title_summary_url(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-16_candidates.csv", _rows("2026-05-16", "a"), columns=BASE_COLUMNS)
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "b"), columns=BASE_COLUMNS)

    assert _run(tmp_path, candidates_dir, status_path, model, ["--min-candidate-schema", "any"]) == 0

    status = _status(status_path)
    assert status["status"] == "refreshed"
    assert len(status["input_candidate_files"]) == 2


def test_best_effort_and_strict_no_eligible_files(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-18_candidates.csv", _rows("2026-05-18", "current"))

    assert _run(tmp_path, candidates_dir, status_path, model, ["--best-effort", "--min-candidate-files", "1"]) == 0
    assert _status(status_path)["status"] == "skipped"
    assert _status(status_path)["error"] == "no_eligible_candidate_files"

    strict_status = tmp_path / "reports" / "_metrics" / "strict_status.json"
    assert _run(tmp_path, candidates_dir, strict_status, model, ["--min-candidate-files", "1"]) == 2
    assert _status(strict_status)["status"] == "skipped"


def test_best_effort_fewer_than_min_candidate_files_skips_and_keeps_existing_model(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "one"))
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text("existing candidate model", encoding="utf-8")

    assert _run(tmp_path, candidates_dir, status_path, model, ["--best-effort", "--min-candidate-files", "2"]) == 0

    status = _status(status_path)
    assert status["status"] == "skipped"
    assert status["error"] == "insufficient_eligible_candidate_files"
    assert model.read_text(encoding="utf-8") == "existing candidate model"


def test_successful_refresh_uses_only_selected_files_and_omits_current_content(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-16_candidates.csv", _rows("2026-05-16", "first"))
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "second"))
    _write_csv(candidates_dir / "2026-05-18_candidates.csv", _rows("2026-05-18", "CURRENT_UNIQUE"))

    assert _run(tmp_path, candidates_dir, status_path, model) == 0

    status = _status(status_path)
    metrics = json.loads((tmp_path / "reports" / "_metrics" / "metrics.json").read_text(encoding="utf-8"))
    disagreements = (tmp_path / "reports" / "_metrics" / "disagreements.csv").read_text(encoding="utf-8")
    assert status["status"] == "refreshed"
    assert model.exists()
    assert str(candidates_dir / "2026-05-18_candidates.csv") not in status["input_candidate_files"]
    assert str(candidates_dir / "2026-05-18_candidates.csv") not in metrics["input_files"]
    assert "CURRENT_UNIQUE" not in disagreements


def test_operating_relevance_model_output_is_refused_and_not_created(tmp_path):
    candidates_dir, status_path, _ = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-16_candidates.csv", _rows("2026-05-16", "a"))
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "b"))
    forbidden = tmp_path / "models" / "relevance.joblib"

    assert _run(tmp_path, candidates_dir, status_path, forbidden) == 2

    assert not forbidden.exists()
    assert _status(status_path)["error"] == "refusing to write models/relevance.joblib"


def test_status_json_includes_selection_audit_fields(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-16_candidates.csv", _rows("2026-05-16", "a"))
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "b"))
    _write_csv(candidates_dir / "2026-05-18_candidates.csv", _rows("2026-05-18", "current"))

    assert _run(tmp_path, candidates_dir, status_path, model) == 0

    status = _status(status_path)
    for field in (
        "report_date", "lookback_days", "min_candidate_schema", "min_candidate_files", "max_candidate_files",
        "discovered_candidate_files", "eligible_candidate_files", "input_candidate_files", "excluded_candidate_files",
        "excluded_reason_counts", "pseudo_label_rows", "trainable_positive", "trainable_negative", "trainable_review",
    ):
        assert field in status
    assert status["lookback_days"] == 21
    assert status["min_candidate_schema"] == "phase1_debug"
    assert status["min_candidate_files"] == 2
    assert status["max_candidate_files"] == 21


def test_refresh_cli_defaults_use_safe_candidate_selection_settings():
    args = refresh.parse_args([])

    assert args.lookback_days == 21
    assert args.min_candidate_schema == "phase1_debug"
    assert args.min_candidate_files == 2
    assert args.max_candidate_files == 21


def test_best_effort_missing_candidates_dir_skips_even_when_candidate_model_exists_without_force(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text("existing candidate model", encoding="utf-8")

    code = refresh.main([
        "--candidates-dir", str(candidates_dir),
        "--model-output", str(model),
        "--status-output", str(status_path),
        "--best-effort",
    ])

    status = _status(status_path)
    assert code == 0
    assert status["status"] == "skipped"
    assert status["error"] == "no_candidate_files"
    assert model.read_text(encoding="utf-8") == "existing candidate model"


def test_invalid_inferred_candidate_date_is_excluded_with_reason(tmp_path):
    candidates_dir, status_path, model = _paths(tmp_path)
    _write_csv(candidates_dir / "2026-05-16_candidates.csv", _rows("2026-05-16", "a"))
    _write_csv(candidates_dir / "2026-05-17_candidates.csv", _rows("2026-05-17", "b"))
    _write_csv(candidates_dir / "2026-99-99_candidates.csv", _rows("2026-99-99", "bad"))

    assert _run(tmp_path, candidates_dir, status_path, model) == 0

    invalid = _excluded(_status(status_path), "invalid_inferred_date")
    assert invalid
    assert invalid[0]["date"] == "2026-99-99"
