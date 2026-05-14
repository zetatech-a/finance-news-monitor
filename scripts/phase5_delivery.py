from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REQUIRED_MARKER_FIELDS = (
    "report_date",
    "sent_at_utc",
    "sent_at_kst",
    "workflow_run_id",
    "workflow_attempt",
    "github_sha",
    "trigger_event",
    "collection_end_hhmm",
    "report_path",
)


def parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def coerce_utc(value: str | None) -> datetime:
    if not value:
        return now_utc()
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def kst_report_date(at_utc: datetime) -> str:
    return at_utc.astimezone(KST).date().isoformat()


def marker_path(report_date: str, reports_dir: str | Path = "reports") -> Path:
    return Path(reports_dir) / "_sent" / f"{report_date}_email_sent.json"


def compute_wait_seconds(
    at_utc: datetime,
    *,
    target_hhmm: str = "0855",
    max_wait_seconds: int = 20 * 60,
) -> int:
    current_kst = at_utc.astimezone(KST)
    target_hhmm = target_hhmm.replace(":", "")
    target = current_kst.replace(
        hour=int(target_hhmm[:2]),
        minute=int(target_hhmm[2:]),
        second=0,
        microsecond=0,
    )
    raw_wait = int((target - current_kst).total_seconds())
    if raw_wait <= 0:
        return 0
    return min(raw_wait, max_wait_seconds)


def should_skip_for_marker(
    marker_exists: bool,
    *,
    event_name: str,
    send_email: bool,
    force_send: bool,
) -> tuple[bool, str]:
    if not marker_exists:
        return False, "marker_absent"
    if event_name == "schedule":
        return True, "scheduled_marker_exists"
    if event_name == "workflow_dispatch" and send_email and not force_send:
        return True, "manual_marker_exists_without_force_send"
    return False, "marker_ignored"


def build_marker(
    *,
    report_date: str,
    sent_at_utc: datetime,
    workflow_run_id: str,
    workflow_attempt: str,
    github_sha: str,
    trigger_event: str,
    collection_end_hhmm: str,
    report_path: str,
) -> dict[str, str]:
    sent_at_utc = sent_at_utc.astimezone(timezone.utc)
    return {
        "report_date": report_date,
        "sent_at_utc": sent_at_utc.isoformat().replace("+00:00", "Z"),
        "sent_at_kst": sent_at_utc.astimezone(KST).isoformat(),
        "workflow_run_id": workflow_run_id,
        "workflow_attempt": workflow_attempt,
        "github_sha": github_sha,
        "trigger_event": trigger_event,
        "collection_end_hhmm": collection_end_hhmm,
        "report_path": report_path,
    }


def write_github_outputs(outputs: dict[str, object], output_path: str | None = None) -> None:
    output_path = output_path or os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        for key, value in outputs.items():
            f.write(f"{key}={value}\n")


def cmd_precheck(args: argparse.Namespace) -> int:
    at_utc = coerce_utc(args.now_utc)
    report_date = kst_report_date(at_utc)
    path = marker_path(report_date, args.reports_dir)
    send_email = args.event_name == "schedule" or parse_bool(args.send_email)
    force_send = parse_bool(args.force_send)
    skip, reason = should_skip_for_marker(
        path.exists(),
        event_name=args.event_name,
        send_email=send_email,
        force_send=force_send,
    )
    outputs = {
        "report_date": report_date,
        "marker_path": path.as_posix(),
        "marker_exists": str(path.exists()).lower(),
        "should_run": str(not skip).lower(),
        "should_send": str(send_email and not skip).lower(),
        "skip_reason": reason,
        "collection_end_hhmm": args.collection_end_hhmm,
        "workflow_started_at_kst": at_utc.astimezone(KST).isoformat(),
    }
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    write_github_outputs(outputs)
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    at_utc = coerce_utc(args.now_utc)
    seconds = 0
    skipped = True
    if args.event_name == "schedule" or parse_bool(args.wait_until_target):
        seconds = compute_wait_seconds(
            at_utc,
            target_hhmm=args.target_hhmm,
            max_wait_seconds=args.max_wait_seconds,
        )
        skipped = seconds == 0
    current_kst = at_utc.astimezone(KST)
    target_hhmm = args.target_hhmm.replace(":", "")
    target_kst = current_kst.replace(
        hour=int(target_hhmm[:2]), minute=int(target_hhmm[2:]), second=0, microsecond=0
    )
    outputs = {
        "current_kst": current_kst.isoformat(),
        "target_kst": target_kst.isoformat(),
        "wait_seconds": seconds,
        "wait_skipped": str(skipped).lower(),
    }
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    write_github_outputs(outputs)
    if seconds > 0 and not args.dry_run:
        time.sleep(seconds)
    return 0


def cmd_mark_sent(args: argparse.Namespace) -> int:
    sent_at = coerce_utc(args.sent_at_utc)
    report_path = Path(args.reports_dir) / f"{args.report_date}.html"
    marker = build_marker(
        report_date=args.report_date,
        sent_at_utc=sent_at,
        workflow_run_id=args.workflow_run_id,
        workflow_attempt=args.workflow_attempt,
        github_sha=args.github_sha,
        trigger_event=args.event_name,
        collection_end_hhmm=args.collection_end_hhmm,
        report_path=report_path.as_posix() if report_path.exists() else "",
    )
    path = marker_path(args.report_date, args.reports_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(marker, ensure_ascii=False, indent=2))
    write_github_outputs({"sent_marker_path": path.as_posix(), "email_sent_at_kst": marker["sent_at_kst"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 GitHub Actions delivery helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    precheck = sub.add_parser("precheck")
    precheck.add_argument("--event-name", required=True)
    precheck.add_argument("--send-email", default="false")
    precheck.add_argument("--force-send", default="false")
    precheck.add_argument("--collection-end-hhmm", default="0855")
    precheck.add_argument("--reports-dir", default="reports")
    precheck.add_argument("--now-utc")
    precheck.set_defaults(func=cmd_precheck)

    wait = sub.add_parser("wait")
    wait.add_argument("--event-name", required=True)
    wait.add_argument("--wait-until-target", default="false")
    wait.add_argument("--target-hhmm", default="0855")
    wait.add_argument("--max-wait-seconds", type=int, default=20 * 60)
    wait.add_argument("--now-utc")
    wait.add_argument("--dry-run", action="store_true")
    wait.set_defaults(func=cmd_wait)

    mark = sub.add_parser("mark-sent")
    mark.add_argument("--report-date", required=True)
    mark.add_argument("--event-name", required=True)
    mark.add_argument("--collection-end-hhmm", required=True)
    mark.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    mark.add_argument("--workflow-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    mark.add_argument("--github-sha", default=os.environ.get("GITHUB_SHA", ""))
    mark.add_argument("--reports-dir", default="reports")
    mark.add_argument("--sent-at-utc")
    mark.set_defaults(func=cmd_mark_sent)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
