from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import phase5_delivery as p5


def test_kst_marker_path_is_computed_from_utc_time():
    at_utc = datetime(2026, 5, 12, 23, 57, tzinfo=timezone.utc)

    report_date = p5.kst_report_date(at_utc)

    assert report_date == "2026-05-13"
    assert p5.marker_path(report_date).as_posix() == "reports/_sent/2026-05-13_email_sent.json"


def test_marker_existence_causes_scheduled_run_to_skip():
    skip, reason = p5.should_skip_for_marker(
        True,
        event_name="schedule",
        send_email=True,
        force_send=False,
    )

    assert skip is True
    assert reason == "scheduled_marker_exists"


def test_force_send_allows_manual_run_despite_marker():
    skip, reason = p5.should_skip_for_marker(
        True,
        event_name="workflow_dispatch",
        send_email=True,
        force_send=True,
    )

    assert skip is False
    assert reason == "marker_ignored"


def test_wait_seconds_is_positive_before_target_and_zero_after_target():
    before = datetime(2026, 5, 12, 23, 49, tzinfo=timezone.utc)  # 08:49 KST
    after = datetime(2026, 5, 13, 0, 7, tzinfo=timezone.utc)  # 09:07 KST

    assert p5.compute_wait_seconds(before, target_hhmm="0855", max_wait_seconds=1200) == 360
    assert p5.compute_wait_seconds(after, target_hhmm="0855", max_wait_seconds=1200) == 0


def test_wait_seconds_is_capped_by_max_wait():
    early = datetime(2026, 5, 12, 23, 30, tzinfo=timezone.utc)  # 08:30 KST

    assert p5.compute_wait_seconds(early, target_hhmm="0855", max_wait_seconds=1200) == 1200


def test_marker_json_contains_required_fields(tmp_path):
    reports_dir = tmp_path / "reports"
    (reports_dir / "2026-05-13.html").parent.mkdir(parents=True)
    (reports_dir / "2026-05-13.html").write_text("<html></html>", encoding="utf-8")

    marker = p5.build_marker(
        report_date="2026-05-13",
        sent_at_utc=datetime(2026, 5, 13, 0, 1, tzinfo=timezone.utc),
        workflow_run_id="123",
        workflow_attempt="2",
        github_sha="abc",
        trigger_event="schedule",
        collection_end_hhmm="0855",
        report_path=(reports_dir / "2026-05-13.html").as_posix(),
    )

    assert set(p5.REQUIRED_MARKER_FIELDS).issubset(marker)
    assert marker["report_date"] == "2026-05-13"
    assert marker["sent_at_utc"] == "2026-05-13T00:01:00Z"
    assert marker["sent_at_kst"].startswith("2026-05-13T09:01:00")
    assert marker["collection_end_hhmm"] == "0855"


def test_workflow_yaml_contains_multiple_staggered_cron_triggers():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    for cron in (
        'cron: "41 23 * * *"',
        'cron: "49 23 * * *"',
        'cron: "57 23 * * *"',
        'cron: "7 0 * * *"',
        'cron: "17 0 * * *"',
    ):
        assert cron in workflow
    assert workflow.count("cron:") == 5
    assert "workflow_dispatch:" in workflow
    assert "force_send:" in workflow


def test_workflow_yaml_contains_concurrency_without_cancel_in_progress():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    assert "concurrency:" in workflow
    assert "group: daily-finance-report-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow


def test_marker_refresh_pulls_fail_closed_before_precheck_and_recheck():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    for step_name in (
        "Pull latest reports and sent markers",
        "Re-pull latest reports and sent markers after wait",
    ):
        start = workflow.index(f"- name: {step_name}")
        end = workflow.find("\n\n      - name:", start)
        block = workflow[start:] if end == -1 else workflow[start:end]
        assert "run: git pull --rebase origin main" in block
        assert "git pull --rebase origin main || true" not in block


def test_scheduled_production_command_uses_end_hhmm_0855():
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")

    assert 'COLLECTION_END_HHMM: "0855"' in workflow
    assert "--end_hhmm \"$COLLECTION_END_HHMM\"" in workflow
    assert "--end_hhmm 0830" not in workflow
    assert "--end_hhmm 0820" not in workflow


@pytest.mark.skipif(
    os.environ.get("ALLOW_DELIVERY_SCHEDULE_CHANGES") == "1",
    reason="daily.yml/src/ml 의도적 수정 중 — ALLOW_DELIVERY_SCHEDULE_CHANGES=1로 opt-out",
)
def test_no_delivery_retry_schedule_or_model_artifact_changed():
    # 우발적인 배송 스케줄/모델 아티팩트 변경을 막는 가드.
    # 의도적으로 수정 중일 때는 위 환경변수로 건너뛸 수 있다(커밋 후에는 자동 통과).
    changed = subprocess.check_output(["git", "diff", "--name-only"], text=True).splitlines()

    forbidden_prefixes = (
        ".github/workflows/daily.yml",
        "src/ml/",
        "models/relevance.joblib",
    )
    forbidden_paths = {
        "src/delivery/email_sender.py",
        "src/clients/naver.py",
    }
    assert not any(path == prefix or path.startswith(prefix) for path in changed for prefix in forbidden_prefixes)
    assert not any(path in forbidden_paths for path in changed)


def test_mark_sent_cli_writes_marker_only_when_invoked(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    (reports_dir / "2026-05-13.html").parent.mkdir(parents=True)
    (reports_dir / "2026-05-13.html").write_text("<html></html>", encoding="utf-8")

    rc = p5.main.__globals__["build_parser"]().parse_args(
        [
            "mark-sent",
            "--report-date",
            "2026-05-13",
            "--event-name",
            "schedule",
            "--collection-end-hhmm",
            "0855",
            "--workflow-run-id",
            "123",
            "--workflow-attempt",
            "1",
            "--github-sha",
            "abc",
            "--reports-dir",
            str(reports_dir),
            "--sent-at-utc",
            "2026-05-13T00:00:00Z",
        ]
    )
    rc.func(rc)

    marker_path = reports_dir / "_sent" / "2026-05-13_email_sent.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(p5.REQUIRED_MARKER_FIELDS).issubset(marker)
    assert marker["report_path"].endswith("2026-05-13.html")
