#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path
from typing import Iterable

SOURCE_COLUMNS = [
    "date", "title", "summary", "url", "query", "score", "prob", "keep", "decision",
    "decision_reason", "relevance_score", "relevance_prob", "matched_hard", "matched_soft",
    "matched_negative",
]
AUTO_COLUMNS = ["auto_label", "auto_label_confidence", "auto_label_reason", "train_weight", "excluded_from_training"]
OUTPUT_COLUMNS = SOURCE_COLUMNS + AUTO_COLUMNS + ["label", "memo"]
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

GENERIC_MACRO_TERMS = ("환율", "금리", "유가", "달러", "원자재")
CORPORATE_INDUSTRY_TERMS = (
    "영업이익", "매출", "순이익", "실적", "원가", "원재료", "수출", "수주", "항공", "조선",
    "식품", "바이오", "게임", "자동차", "반도체", "해운", "철강",
)
NON_FINANCE_TERMS = ("연예", "스포츠", "축구", "야구", "드라마", "영화", "맛집", "여행", "쇼핑", "패션")
FINANCIAL_CONTEXT_TERMS = (
    "은행", "저축은행", "보험", "카드", "카드사", "카드론", "금융", "금융위", "금감원", "증권",
    "여신", "캐피탈", "대부", "사금융", "채권추심", "PF", "부실채권", "가계대출",
)
REGULATORY_ACTION_TERMS = ("검사", "제재", "과징금", "시정명령", "행정처분", "제도", "정책", "규제", "감독", "발표", "조치")


def normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def dedupe_key(row: dict[str, str]) -> str:
    url = (row.get("url") or "").strip()
    return f"url:{url}" if url else f"title:{normalize_title(row.get('title', ''))}"


def infer_date(path: Path) -> str:
    match = DATE_RE.search(path.name)
    return match.group(1) if match else ""


def discover_input_files(candidates_dir: Path, explicit_inputs: list[Path] | None = None) -> list[Path]:
    if explicit_inputs:
        return sorted(explicit_inputs, key=lambda p: str(p))
    if not candidates_dir.exists():
        return []
    return sorted(candidates_dir.glob("*.csv"), key=lambda p: str(p))


def _has_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _num(row: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def _is_nonempty(value: str | None) -> bool:
    return bool((value or "").strip())


def _strong_domain_keep(text: str) -> str | None:
    pairs: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
        (("저축은행",), ("연체율", "PF", "부실채권"), "strong_keep_savings_bank_risk"),
        (("은행",), ("가계대출", "예대금리차", "연체"), "strong_keep_bank_consumer_credit"),
        (("보험",), ("킥스", "K-ICS", "실손", "보험료"), "strong_keep_insurance_risk"),
        (("카드론", "카드사"), ("연체", "수수료"), "strong_keep_card_credit"),
        (("금감원", "금융위"), REGULATORY_ACTION_TERMS, "strong_keep_regulatory_action"),
        (("대부업", "불법사금융", "채권추심", "최고금리"), ("" ,), "strong_keep_lending_consumer_protection"),
    )
    for anchors, signals, reason in pairs:
        if _has_any(text, anchors) and (signals == ("",) or _has_any(text, signals)):
            return reason
    return None


def _is_corporate_macro_noise(text: str) -> bool:
    return _has_any(text, GENERIC_MACRO_TERMS) and _has_any(text, CORPORATE_INDUSTRY_TERMS) and not _has_any(text, FINANCIAL_CONTEXT_TERMS)


def _too_short(row: dict[str, str]) -> bool:
    title = (row.get("title") or "").strip()
    summary = (row.get("summary") or "").strip()
    return len(title) < 6 and len(summary) < 12


def assign_pseudo_label(row: dict[str, str]) -> dict[str, str]:
    title = row.get("title", "")
    summary = row.get("summary", "")
    text = f"{title}\n{summary}"
    decision = (row.get("decision") or "").strip().lower()
    reason = (row.get("decision_reason") or "").strip()
    score = _num(row, "relevance_score", "score")
    matched_hard = row.get("matched_hard", "")
    matched_negative = row.get("matched_negative", "")

    keep_reason = _strong_domain_keep(text)
    drop_reason: str | None = None
    if _is_nonempty(matched_negative) or reason == "rule_drop_negative_signal":
        drop_reason = "drop_negative_signal"
    elif _has_any(text, NON_FINANCE_TERMS):
        drop_reason = "drop_obvious_non_finance"
    elif reason == "rule_drop_no_financial_anchor":
        drop_reason = "drop_no_financial_anchor"
    elif _is_corporate_macro_noise(text):
        drop_reason = "drop_corporate_macro_noise"
    elif decision == "drop" and score is not None and score <= 2:
        drop_reason = "drop_score_le_2"

    rule_keep = decision == "keep" and score is not None and score >= 6 and _is_nonempty(matched_hard) and not _is_nonempty(matched_negative)
    conflict = bool(keep_reason or rule_keep) and bool(drop_reason)

    if conflict:
        label, conf, label_reason = "review", 0.0, f"review_conflicting_signals:{keep_reason or 'rule_keep'}:{drop_reason}"
    elif keep_reason:
        label, conf, label_reason = "1", 0.95, keep_reason
    elif rule_keep:
        label, conf, label_reason = "1", (0.85 if score >= 8 else 0.75), "rule_keep_score_ge_6_with_hard_match"
    elif drop_reason:
        conf = 0.85 if drop_reason == "drop_corporate_macro_noise" else 0.95 if drop_reason in {"drop_negative_signal", "drop_obvious_non_finance"} else 0.75
        label, conf, label_reason = "0", conf, drop_reason
    elif _too_short(row):
        label, conf, label_reason = "review", 0.0, "review_text_too_short"
    elif score is not None and 3 <= score <= 5:
        label, conf, label_reason = "review", 0.0, "review_mid_score_ambiguous"
    elif _has_any(text, GENERIC_MACRO_TERMS) and not _has_any(text, FINANCIAL_CONTEXT_TERMS):
        label, conf, label_reason = "review", 0.0, "review_generic_macro_only"
    elif _has_any(text, ("금융위", "금감원")) and not _has_any(text, REGULATORY_ACTION_TERMS):
        label, conf, label_reason = "review", 0.0, "review_regulator_without_action_context"
    elif _has_any(text, ("회사채", "환율", "금리")):
        label, conf, label_reason = "review", 0.0, "review_ambiguous_market_context"
    else:
        label, conf, label_reason = "review", 0.0, "review_no_conservative_rule_matched"

    excluded = label == "review"
    return {
        "auto_label": label,
        "auto_label_confidence": f"{conf:.2f}",
        "auto_label_reason": label_reason,
        "train_weight": "0.00" if excluded else f"{conf:.2f}",
        "excluded_from_training": "true" if excluded else "false",
    }


def read_candidate_rows(input_files: Iterable[Path]) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    total = 0
    for path in input_files:
        file_date = infer_date(path)
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                total += 1
                row = {column: (raw.get(column) or "") for column in SOURCE_COLUMNS}
                if not row["date"]:
                    row["date"] = file_date
                row.update(assign_pseudo_label(row))
                row["label"] = row["auto_label"]
                row["memo"] = "auto-generated pseudo-label"
                rows.append(row)
    return rows, total


def deduplicate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def deterministic_sample(rows: list[dict[str, str]], max_rows: int, seed: int) -> list[dict[str, str]]:
    if max_rows <= 0 or len(rows) <= max_rows:
        sampled = list(rows)
    else:
        rng = random.Random(seed)
        sampled = rng.sample(rows, max_rows)
    sampled.sort(key=lambda r: (r.get("date", ""), r.get("url", ""), normalize_title(r.get("title", ""))))
    return sampled


def write_rows(output: Path, rows: list[dict[str, str]], *, force: bool) -> None:
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}. Use --force to overwrite.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows([{column: row.get(column, "") for column in OUTPUT_COLUMNS} for row in rows])


def build_pseudo_labels(candidates_dir: Path, inputs: list[Path] | None, max_rows: int, seed: int) -> tuple[list[dict[str, str]], dict[str, object]]:
    input_files = discover_input_files(candidates_dir, inputs)
    rows, total = read_candidate_rows(input_files)
    deduped = deduplicate_rows(rows)
    sampled = deterministic_sample(deduped, max_rows, seed)
    return sampled, {
        "input_files": [str(p) for p in input_files],
        "rows_read": total,
        "rows_after_dedupe": len(deduped),
        "rows_written": len(sampled),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate automatic conservative relevance pseudo-labels from candidate CSVs.")
    parser.add_argument("--candidates-dir", type=Path, default=Path("reports/_candidates"))
    parser.add_argument("--input", dest="inputs", action="append", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/auto_labels/relevance_pseudo_labels.csv"))
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, stats = build_pseudo_labels(args.candidates_dir, args.inputs, args.max_rows, args.seed)
        write_rows(args.output, rows, force=args.force)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"input_files={len(stats['input_files'])} rows_read={stats['rows_read']} rows_written={stats['rows_written']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
