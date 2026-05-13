#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Iterable

OUTPUT_COLUMNS = [
    "date",
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
    "label",
    "memo",
]

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def dedupe_key(row: dict[str, str]) -> str:
    url = (row.get("url") or "").strip()
    if url:
        return f"url:{url}"
    return f"title:{normalize_title(row.get('title', ''))}"


def infer_date(path: Path) -> str:
    match = DATE_RE.search(path.name)
    return match.group(1) if match else ""


def discover_input_files(candidates_dir: Path, explicit_inputs: list[Path] | None = None) -> list[Path]:
    if explicit_inputs:
        return sorted(explicit_inputs, key=lambda p: str(p))
    if not candidates_dir.exists():
        return []
    return sorted(candidates_dir.glob("*.csv"), key=lambda p: str(p))


def _score_bucket(row: dict[str, str]) -> str:
    raw = (row.get("score") or row.get("relevance_score") or "").strip()
    if raw == "":
        return "score:blank"
    try:
        score = float(raw)
    except ValueError:
        return "score:other"
    if score < 4:
        return "score:low"
    if score < 8:
        return "score:mid"
    return "score:high"


def _date_bucket(row: dict[str, str]) -> str:
    date = (row.get("date") or "").strip()
    return date if date else "date:blank"


def _stratum_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    decision = (row.get("decision") or "").strip().lower()
    keep = (row.get("keep") or "").strip().lower()
    if not decision:
        if keep in {"1", "true", "yes", "keep"}:
            decision = "keep"
        elif keep in {"0", "false", "no", "drop"}:
            decision = "drop"
        else:
            decision = "unknown"
    reason = (row.get("decision_reason") or "").strip() or "reason:blank"
    return decision, reason, _score_bucket(row), _date_bucket(row)


def read_candidate_rows(input_files: Iterable[Path]) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    total_rows = 0
    for path in input_files:
        file_date = infer_date(path)
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                total_rows += 1
                row = {column: (raw.get(column) or "") for column in OUTPUT_COLUMNS}
                if not row["date"]:
                    row["date"] = file_date
                row["label"] = ""
                row["memo"] = ""
                rows.append(row)
    return rows, total_rows


def deduplicate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def stratified_sample(rows: list[dict[str, str]], max_samples: int, seed: int) -> list[dict[str, str]]:
    if max_samples <= 0 or len(rows) <= max_samples:
        return list(rows)

    rng = random.Random(seed)
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[_stratum_key(row)].append(row)

    queues: list[tuple[tuple[str, str, str, str], deque[dict[str, str]]]] = []
    for key in sorted(groups):
        group_rows = groups[key]
        rng.shuffle(group_rows)
        queues.append((key, deque(group_rows)))

    sampled: list[dict[str, str]] = []
    while queues and len(sampled) < max_samples:
        next_queues: list[tuple[tuple[str, str, str, str], deque[dict[str, str]]]] = []
        for key, queue in queues:
            if len(sampled) >= max_samples:
                next_queues.append((key, queue))
                continue
            if queue:
                sampled.append(queue.popleft())
            if queue:
                next_queues.append((key, queue))
        queues = next_queues

    sampled.sort(key=lambda row: (row.get("date", ""), row.get("url", ""), normalize_title(row.get("title", ""))))
    return sampled


def write_sample(output: Path, rows: list[dict[str, str]], *, force: bool) -> None:
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}. Use --force to overwrite.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows([{column: row.get(column, "") for column in OUTPUT_COLUMNS} for row in rows])


def keep_drop_counts(rows: Iterable[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        decision = (row.get("decision") or "").strip().lower()
        keep = (row.get("keep") or "").strip().lower()
        if decision in {"keep", "drop"}:
            counts[decision] += 1
        elif keep in {"1", "true", "yes", "keep"}:
            counts["keep"] += 1
        elif keep in {"0", "false", "no", "drop"}:
            counts["drop"] += 1
    return counts


def build_sample(candidates_dir: Path, inputs: list[Path] | None, max_samples: int, seed: int) -> tuple[list[dict[str, str]], dict[str, object]]:
    input_files = discover_input_files(candidates_dir, inputs)
    rows, rows_read = read_candidate_rows(input_files)
    deduped = deduplicate_rows(rows)
    sampled = stratified_sample(deduped, max_samples, seed)
    counts = keep_drop_counts(sampled)
    summary = {
        "candidate_files_read": len(input_files),
        "rows_read": rows_read,
        "deduplicated_rows": len(deduped),
        "rows_written": len(sampled),
        "keep_count": counts.get("keep", 0),
        "drop_count": counts.get("drop", 0),
    }
    return sampled, summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a human relevance-labeling CSV from candidate CSVs.")
    parser.add_argument("--candidates-dir", type=Path, default=Path("reports/_candidates"))
    parser.add_argument("--input", type=Path, action="append", dest="inputs", help="Explicit candidate CSV input path; may be repeated.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-samples", type=int, default=700)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, summary = build_sample(args.candidates_dir, args.inputs, args.max_samples, args.seed)
        write_sample(args.output, rows, force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Relevance labeling sample generated")
    print(f"candidate files read: {summary['candidate_files_read']}")
    print(f"rows read: {summary['rows_read']}")
    print(f"deduplicated rows: {summary['deduplicated_rows']}")
    print(f"rows written: {summary['rows_written']}")
    print(f"keep/drop counts: keep={summary['keep_count']} drop={summary['drop_count']}")
    print(f"output path: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
