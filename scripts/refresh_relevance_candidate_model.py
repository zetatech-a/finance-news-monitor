#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import joblib

from scripts.generate_relevance_pseudo_labels import build_pseudo_labels, discover_input_files, infer_date, write_rows
from scripts.train_relevance_candidate_model import (
    _fit_model,
    evaluate,
    load_rows,
    usable_rows,
    write_disagreements,
    write_json,
    write_report,
)

FORBIDDEN_MODEL_OUTPUT = Path("models/relevance.joblib")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_forbidden_model_output(path: Path) -> bool:
    try:
        return path.resolve() == FORBIDDEN_MODEL_OUTPUT.resolve()
    except FileNotFoundError:
        return path.absolute() == FORBIDDEN_MODEL_OUTPUT.absolute()


def _split_candidate_files(candidates_dir: Path, report_date: str | None) -> tuple[list[Path], list[Path]]:
    discovered = discover_input_files(candidates_dir, None)
    if not report_date:
        return discovered, []
    included: list[Path] = []
    excluded: list[Path] = []
    for path in discovered:
        if infer_date(path) == report_date:
            excluded.append(path)
        else:
            included.append(path)
    return included, excluded


def _base_status(args: argparse.Namespace, started_at: str, input_files: Sequence[Path], excluded_files: Sequence[Path], model_exists_before: bool) -> dict[str, object]:
    return {
        "report_date": args.report_date or "",
        "started_at_utc": started_at,
        "finished_at_utc": "",
        "status": "failed",
        "best_effort": bool(args.best_effort),
        "candidates_dir": str(args.candidates_dir),
        "input_candidate_files": [str(path) for path in input_files],
        "excluded_candidate_files": [str(path) for path in excluded_files],
        "pseudo_label_rows": 0,
        "trainable_positive": 0,
        "trainable_negative": 0,
        "trainable_review": 0,
        "model_output": str(args.model_output),
        "model_exists_before": model_exists_before,
        "model_exists_after": args.model_output.exists(),
        "metrics_output": str(args.metrics_output),
        "report_output": str(args.report_output),
        "disagreements_output": str(args.disagreements_output),
    }


def _write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _finish(status_path: Path, payload: dict[str, object], status: str, message: str | None = None) -> None:
    payload["status"] = status
    payload["finished_at_utc"] = _utc_now()
    payload["model_exists_after"] = Path(str(payload["model_output"])).exists()
    if message:
        payload["error"] = message
    _write_status(status_path, payload)


def refresh(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if _is_forbidden_model_output(args.model_output):
        started_at = _utc_now()
        status = _base_status(args, started_at, [], [], args.model_output.exists())
        _finish(args.status_output, status, "failed", "refusing to write models/relevance.joblib")
        print("error: refusing to write models/relevance.joblib", file=sys.stderr)
        return 0 if args.best_effort else 2, status

    started_at = _utc_now()
    input_files, excluded_files = _split_candidate_files(args.candidates_dir, args.report_date)
    status = _base_status(args, started_at, input_files, excluded_files, args.model_output.exists())

    try:
        if args.model_output.exists() and not args.force:
            raise FileExistsError(f"Model output already exists: {args.model_output}. Use --force to overwrite.")
        if not input_files:
            _finish(args.status_output, status, "skipped", "no historical candidate CSV files found")
            print("warning: candidate model refresh skipped: no historical candidate CSV files found", file=sys.stderr)
            return 0 if args.best_effort else 2, status

        with tempfile.TemporaryDirectory(prefix="relevance_candidate_refresh_") as tmp:
            pseudo_labels_path = Path(tmp) / "relevance_pseudo_labels.csv"
            pseudo_rows, _ = build_pseudo_labels(args.candidates_dir, list(input_files), args.max_rows, args.seed)
            status["pseudo_label_rows"] = len(pseudo_rows)
            write_rows(pseudo_labels_path, pseudo_rows, force=True)

            rows, warnings = load_rows([pseudo_labels_path])
            used, ignored = usable_rows(rows)
            labels = [(row.get("auto_label") or "").strip() for row in rows]
            pos = sum(1 for row in used if row["auto_label"] == "1")
            neg = sum(1 for row in used if row["auto_label"] == "0")
            review = sum(1 for label in labels if label == "review")
            status.update({"trainable_positive": pos, "trainable_negative": neg, "trainable_review": review})

            skip_reason = ""
            if not used:
                skip_reason = "no usable rows remain after excluding review/blank labels"
            elif pos == 0 or neg == 0:
                skip_reason = "training data must contain both positive and negative classes"
            elif pos < args.min_positive:
                skip_reason = f"positive count {pos} is below --min-positive {args.min_positive}"
            elif neg < args.min_negative:
                skip_reason = f"negative count {neg} is below --min-negative {args.min_negative}"
            if skip_reason:
                _finish(args.status_output, status, "skipped", skip_reason)
                print(f"warning: candidate model refresh skipped: {skip_reason}", file=sys.stderr)
                return 0 if args.best_effort else 2, status

            eval_payload, _, _, eval_warnings = evaluate(used, args.seed, args.test_size, args.threshold)
            warnings.extend(eval_warnings)
            model = _fit_model(used, args.seed)
            args.model_output.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(model, args.model_output)

            payload: dict[str, object] = {
                "input_files": [str(path) for path in input_files],
                "excluded_input_files": [str(path) for path in excluded_files],
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

        _finish(args.status_output, status, "refreshed")
        print(
            f"candidate model refreshed input_files={len(input_files)} excluded_files={len(excluded_files)} "
            f"pseudo_label_rows={status['pseudo_label_rows']} positive={status['trainable_positive']} "
            f"negative={status['trainable_negative']} review={status['trainable_review']} "
            f"model_output={args.model_output} status_output={args.status_output}"
        )
        return 0, status
    except Exception as exc:
        message = str(exc)
        _finish(args.status_output, status, "failed", message)
        print(f"warning: candidate model refresh failed: {message}" if args.best_effort else f"error: {message}", file=sys.stderr)
        return 0 if args.best_effort else 2, status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the automatic candidate relevance model from historical candidate CSVs.")
    parser.add_argument("--candidates-dir", type=Path, default=Path("reports/_candidates"))
    parser.add_argument("--model-output", type=Path, default=Path("models/relevance_candidate.joblib"))
    parser.add_argument("--metrics-output", type=Path, default=Path("reports/_metrics/relevance_candidate_eval.json"))
    parser.add_argument("--report-output", type=Path, default=Path("reports/_metrics/relevance_candidate_eval.txt"))
    parser.add_argument("--disagreements-output", type=Path, default=Path("reports/_metrics/relevance_disagreements.csv"))
    parser.add_argument("--status-output", type=Path, default=Path("reports/_metrics/candidate_model_refresh.json"))
    parser.add_argument("--report-date", default="")
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-positive", type=int, default=50)
    parser.add_argument("--min-negative", type=int, default=50)
    parser.add_argument("--threshold", type=float, default=0.65)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--max-disagreements", type=int, default=5000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--best-effort", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    code, _ = refresh(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
