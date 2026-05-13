from __future__ import annotations

import csv
import json

from scripts import make_relevance_labeling_sample as sample
from scripts import validate_relevance_labels as validate


def _write_csv(path, rows, fieldnames=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = [
            "title",
            "summary",
            "url",
            "query",
            "score",
            "prob",
            "keep",
            "decision",
            "decision_reason",
            "relevance_score",
            "relevance_prob",
            "matched_hard",
            "matched_soft",
            "matched_negative",
        ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_sample_generator_reads_candidate_csv_files_and_writes_stable_columns(tmp_path):
    candidates_dir = tmp_path / "reports" / "_candidates"
    _write_csv(
        candidates_dir / "2026-05-11_candidates.csv",
        [
            {
                "title": "은행 기사",
                "summary": "요약",
                "url": "https://example.com/a",
                "query": "은행",
                "score": "5",
                "prob": "",
                "keep": "1",
                "decision": "keep",
                "decision_reason": "rule_keep_score_ge_threshold",
                "relevance_score": "5",
                "relevance_prob": "",
                "matched_hard": "은행",
                "matched_soft": "",
                "matched_negative": "",
            }
        ],
    )
    output = tmp_path / "data" / "labeling" / "sample.csv"

    exit_code = sample.main([
        "--candidates-dir",
        str(candidates_dir),
        "--output",
        str(output),
        "--max-samples",
        "10",
    ])

    assert exit_code == 0
    with output.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == sample.OUTPUT_COLUMNS
    assert rows[0]["date"] == "2026-05-11"
    assert rows[0]["label"] == ""
    assert rows[0]["memo"] == ""


def test_sample_generator_deduplicates_rows_by_url(tmp_path):
    candidates_dir = tmp_path / "candidates"
    _write_csv(
        candidates_dir / "2026-05-10_candidates.csv",
        [
            {"title": "첫 기사", "summary": "a", "url": "https://example.com/dup", "decision": "keep"},
            {"title": "둘째 기사", "summary": "b", "url": "https://example.com/dup", "decision": "drop"},
            {"title": "셋째 기사", "summary": "c", "url": "https://example.com/unique", "decision": "drop"},
        ],
    )
    output = tmp_path / "sample.csv"

    assert sample.main(["--candidates-dir", str(candidates_dir), "--output", str(output)]) == 0

    rows = _read_csv(output)
    assert len(rows) == 2
    assert {row["url"] for row in rows} == {"https://example.com/dup", "https://example.com/unique"}


def test_sample_generator_is_deterministic_with_same_seed(tmp_path):
    candidates_dir = tmp_path / "candidates"
    rows = [
        {
            "title": f"기사 {i}",
            "summary": "요약",
            "url": f"https://example.com/{i}",
            "score": str(i % 10),
            "keep": str(i % 2),
            "decision": "keep" if i % 2 else "drop",
            "decision_reason": f"reason_{i % 3}",
        }
        for i in range(20)
    ]
    _write_csv(candidates_dir / "2026-05-09_candidates.csv", rows)
    output_one = tmp_path / "one.csv"
    output_two = tmp_path / "two.csv"

    args = ["--candidates-dir", str(candidates_dir), "--max-samples", "7", "--seed", "123"]
    assert sample.main([*args, "--output", str(output_one)]) == 0
    assert sample.main([*args, "--output", str(output_two)]) == 0

    assert output_one.read_text(encoding="utf-8") == output_two.read_text(encoding="utf-8")


def test_sample_generator_refuses_to_overwrite_without_force(tmp_path):
    candidates_dir = tmp_path / "candidates"
    _write_csv(candidates_dir / "2026-05-08_candidates.csv", [{"title": "기사", "summary": "요약", "url": "https://example.com/a"}])
    output = tmp_path / "sample.csv"
    output.write_text("existing", encoding="utf-8")

    exit_code = sample.main(["--candidates-dir", str(candidates_dir), "--output", str(output)])

    assert exit_code == 2
    assert output.read_text(encoding="utf-8") == "existing"
    assert sample.main(["--candidates-dir", str(candidates_dir), "--output", str(output), "--force"]) == 0


def test_validator_passes_file_with_valid_labels(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(
        input_path,
        [
            {"title": "긍정", "summary": "요약", "url": "https://example.com/1", "label": "1"},
            {"title": "부정", "summary": "요약", "url": "https://example.com/0", "label": "0"},
            {"title": "검토", "summary": "요약", "url": "https://example.com/r", "label": "review"},
        ],
        fieldnames=["title", "summary", "url", "label"],
    )

    metrics, warnings, errors = validate.validate_file(input_path, allow_blank=False)

    assert errors == []
    assert warnings
    assert metrics["labeled_rows"] == 3
    assert metrics["positive_labels"] == 1
    assert metrics["negative_labels"] == 1
    assert metrics["review_labels"] == 1


def test_validator_fails_on_invalid_labels(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(
        input_path,
        [{"title": "기사", "summary": "요약", "url": "https://example.com/a", "label": "yes"}],
        fieldnames=["title", "summary", "url", "label"],
    )

    metrics, warnings, errors = validate.validate_file(input_path, allow_blank=False)

    assert warnings
    assert metrics["labeled_rows"] == 0
    assert any("row 2: invalid label 'yes'" == error for error in errors)


def test_validator_allows_blank_labels_only_when_allow_blank_is_provided(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(
        input_path,
        [{"title": "기사", "summary": "요약", "url": "https://example.com/a", "label": ""}],
        fieldnames=["title", "summary", "url", "label"],
    )

    allow_metrics, _, allow_errors = validate.validate_file(input_path, allow_blank=True)
    strict_metrics, _, strict_errors = validate.validate_file(input_path, allow_blank=False)

    assert allow_errors == []
    assert allow_metrics["unlabeled_rows"] == 1
    assert strict_metrics["unlabeled_rows"] == 1
    assert "row 2: blank label" in strict_errors


def test_validator_reports_duplicate_rows_and_label_counts(tmp_path):
    input_path = tmp_path / "labels.csv"
    _write_csv(
        input_path,
        [
            {"title": "A", "summary": "요약", "url": "https://example.com/dup", "label": "1"},
            {"title": "B", "summary": "요약", "url": "https://example.com/dup", "label": "0"},
            {"title": "C", "summary": "요약", "url": "", "label": "review"},
            {"title": "C", "summary": "요약", "url": "", "label": ""},
        ],
        fieldnames=["title", "summary", "url", "label"],
    )

    metrics, _, errors = validate.validate_file(input_path, allow_blank=True)

    assert errors == []
    assert metrics["total_rows"] == 4
    assert metrics["positive_labels"] == 1
    assert metrics["negative_labels"] == 1
    assert metrics["review_labels"] == 1
    assert metrics["unlabeled_rows"] == 1
    assert metrics["duplicate_rows"] == 2
    assert metrics["missing_url_rows"] == 2


def test_validator_writes_metrics_json_when_requested(tmp_path):
    input_path = tmp_path / "labels.csv"
    metrics_path = tmp_path / "reports" / "_metrics" / "validation.json"
    _write_csv(
        input_path,
        [{"title": "기사", "summary": "요약", "url": "https://example.com/a", "label": "1"}],
        fieldnames=["title", "summary", "url", "label"],
    )

    exit_code = validate.main([
        "--input",
        str(input_path),
        "--metrics-output",
        str(metrics_path),
    ])

    assert exit_code == 0
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["total_rows"] == 1
    assert payload["metrics"]["positive_labels"] == 1
    assert payload["warnings"]
    assert payload["errors"] == []
