#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

REQUIRED_COLUMNS = ["title", "summary", "url", "label"]
VALID_LABELS = {"1", "0", "review"}
RECOMMENDED_MINIMUMS = {"positive_labels": 300, "negative_labels": 300, "review_labels": 100}


def normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def dedupe_key(row: dict[str, str]) -> str:
    url = (row.get("url") or "").strip()
    if url:
        return f"url:{url}"
    return f"title:{normalize_title(row.get('title', ''))}"


def validate_file(input_path: Path, *, allow_blank: bool, strict_min_counts: bool = False) -> tuple[dict[str, int | list[str]], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, int | list[str]] = {
        "total_rows": 0,
        "labeled_rows": 0,
        "unlabeled_rows": 0,
        "positive_labels": 0,
        "negative_labels": 0,
        "review_labels": 0,
        "duplicate_rows": 0,
        "missing_title_rows": 0,
        "missing_url_rows": 0,
        "invalid_rows": [],
    }

    seen: set[str] = set()
    with input_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing_columns:
            errors.append(f"missing required columns: {', '.join(missing_columns)}")
            return metrics, warnings, errors

        invalid_rows: list[str] = []
        for index, row in enumerate(reader, start=2):
            metrics["total_rows"] = int(metrics["total_rows"]) + 1
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            label = (row.get("label") or "").strip().lower()

            if not title:
                metrics["missing_title_rows"] = int(metrics["missing_title_rows"]) + 1
            if not url:
                metrics["missing_url_rows"] = int(metrics["missing_url_rows"]) + 1

            key = dedupe_key(row)
            if key in seen:
                metrics["duplicate_rows"] = int(metrics["duplicate_rows"]) + 1
            else:
                seen.add(key)

            if not label:
                metrics["unlabeled_rows"] = int(metrics["unlabeled_rows"]) + 1
                if not allow_blank:
                    invalid_rows.append(f"row {index}: blank label")
                continue

            if label not in VALID_LABELS:
                invalid_rows.append(f"row {index}: invalid label {label!r}")
                continue

            metrics["labeled_rows"] = int(metrics["labeled_rows"]) + 1
            if label == "1":
                metrics["positive_labels"] = int(metrics["positive_labels"]) + 1
            elif label == "0":
                metrics["negative_labels"] = int(metrics["negative_labels"]) + 1
            elif label == "review":
                metrics["review_labels"] = int(metrics["review_labels"]) + 1

    metrics["invalid_rows"] = invalid_rows
    errors.extend(invalid_rows)

    for metric, minimum in RECOMMENDED_MINIMUMS.items():
        actual = int(metrics[metric])
        if actual < minimum:
            warning = f"{metric} below recommended minimum: {actual} < {minimum}"
            warnings.append(warning)
            if strict_min_counts:
                errors.append(warning)

    return metrics, warnings, errors


def write_metrics(path: Path, metrics: dict[str, int | list[str]], warnings: list[str], errors: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metrics": metrics, "warnings": warnings, "errors": errors}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_summary(metrics: dict[str, int | list[str]], warnings: list[str], errors: list[str]) -> None:
    print("Relevance label validation summary")
    for key in (
        "total_rows",
        "labeled_rows",
        "unlabeled_rows",
        "positive_labels",
        "negative_labels",
        "review_labels",
        "duplicate_rows",
        "missing_title_rows",
        "missing_url_rows",
    ):
        print(f"{key}: {metrics[key]}")
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("errors:")
        for error in errors:
            print(f"- {error}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a relevance labeling CSV before model training.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--allow-blank", action="store_true")
    parser.add_argument("--strict-min-counts", action="store_true")
    parser.add_argument("--metrics-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        metrics, warnings, errors = validate_file(
            args.input,
            allow_blank=args.allow_blank,
            strict_min_counts=args.strict_min_counts,
        )
        if args.metrics_output:
            write_metrics(args.metrics_output, metrics, warnings, errors)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_summary(metrics, warnings, errors)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
