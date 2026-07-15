"""reports/ 보존 정책: 오래된 리포트·관측성 파일을 정리한다.

- 일별 리포트(YYYY-MM-DD.md / .html): 기본 180일 보관
- _candidates / _metrics / _sent 아래 날짜 접두 파일: 기본 90일 보관
  (후보 모델 학습 lookback이 21일이므로 90일이면 충분한 여유)
- index.html, _cache/는 절대 건드리지 않는다
- 파일명이 YYYY-MM-DD로 시작하지 않는 파일은 건너뛴다

daily.yml에서 리포트 생성 후, 커밋 전에 실행된다.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

DEFAULT_REPORT_KEEP_DAYS = 180
DEFAULT_ARTIFACT_KEEP_DAYS = 90
ARTIFACT_SUBDIRS = ("_candidates", "_metrics", "_sent")

_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _file_date(path: Path) -> date | None:
    match = _DATE_PREFIX_RE.match(path.name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def collect_prunable(
    reports_dir: Path,
    *,
    reference: date,
    report_keep_days: int = DEFAULT_REPORT_KEEP_DAYS,
    artifact_keep_days: int = DEFAULT_ARTIFACT_KEEP_DAYS,
) -> list[Path]:
    report_cutoff = reference - timedelta(days=report_keep_days)
    artifact_cutoff = reference - timedelta(days=artifact_keep_days)
    prunable: list[Path] = []

    for path in sorted(reports_dir.glob("*.md")) + sorted(reports_dir.glob("*.html")):
        if path.name == "index.html":
            continue
        file_date = _file_date(path)
        if file_date is not None and file_date < report_cutoff:
            prunable.append(path)

    for subdir in ARTIFACT_SUBDIRS:
        folder = reports_dir / subdir
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            file_date = _file_date(path)
            if file_date is not None and file_date < artifact_cutoff:
                prunable.append(path)

    return prunable


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prune old report files")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument(
        "--reference-date",
        type=str,
        default=None,
        help="기준일(YYYY-MM-DD, 기본: 오늘). 이 날짜에서 보존 기간을 역산한다",
    )
    parser.add_argument("--report-keep-days", type=int, default=DEFAULT_REPORT_KEEP_DAYS)
    parser.add_argument(
        "--artifact-keep-days",
        type=int,
        default=DEFAULT_ARTIFACT_KEEP_DAYS,
        help="_candidates/_metrics/_sent 보존 기간. 후보 모델 lookback(21일)보다 커야 한다",
    )
    parser.add_argument("--dry-run", action="store_true", help="삭제 대상만 출력하고 지우지 않음")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.report_keep_days < 1 or args.artifact_keep_days < 1:
        print("keep-days must be >= 1", file=sys.stderr)
        return 2
    if not args.reports_dir.is_dir():
        print(f"reports dir not found: {args.reports_dir} (nothing to prune)")
        return 0

    reference = (
        date.fromisoformat(args.reference_date) if args.reference_date else date.today()
    )
    prunable = collect_prunable(
        args.reports_dir,
        reference=reference,
        report_keep_days=args.report_keep_days,
        artifact_keep_days=args.artifact_keep_days,
    )

    for path in prunable:
        print(f"{'would prune' if args.dry_run else 'prune'}: {path}")
        if not args.dry_run:
            path.unlink()

    print(
        f"pruned={0 if args.dry_run else len(prunable)} candidates={len(prunable)} "
        f"reference={reference.isoformat()} "
        f"report_keep_days={args.report_keep_days} artifact_keep_days={args.artifact_keep_days}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
