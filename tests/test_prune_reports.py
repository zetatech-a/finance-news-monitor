from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.prune_reports import collect_prunable, main

REFERENCE = date(2026, 7, 15)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def _setup_reports(tmp_path: Path) -> Path:
    reports = tmp_path / "reports"
    # 일별 리포트: 오래된 것(2026-01-01, 195일 전)과 최근 것(2026-07-14)
    _touch(reports / "2026-01-01.md")
    _touch(reports / "2026-01-01.html")
    _touch(reports / "2026-07-14.md")
    _touch(reports / "2026-07-14.html")
    # 절대 지우면 안 되는 파일들
    _touch(reports / "index.html")
    _touch(reports / "_cache" / "summary_cache.json")
    # 부속물: 오래된 것(2026-03-01, 136일 전)과 최근 것(2026-07-01, 14일 전)
    _touch(reports / "_candidates" / "2026-03-01_candidates.csv")
    _touch(reports / "_candidates" / "2026-07-01_candidates.csv")
    _touch(reports / "_metrics" / "2026-03-01_quality_metrics.json")
    _touch(reports / "_metrics" / "2026-07-01_quality_metrics.json")
    _touch(reports / "_sent" / "2026-03-01_email_sent.json")
    _touch(reports / "_sent" / "2026-07-01_email_sent.json")
    # 날짜 접두어 없는 파일은 건너뜀
    _touch(reports / "_metrics" / "relevance_candidate_eval.json")
    return reports


def test_collect_prunable_selects_only_expired_dated_files(tmp_path):
    reports = _setup_reports(tmp_path)

    prunable = {p.relative_to(reports).as_posix() for p in collect_prunable(
        reports, reference=REFERENCE, report_keep_days=180, artifact_keep_days=90
    )}

    assert prunable == {
        "2026-01-01.md",
        "2026-01-01.html",
        "_candidates/2026-03-01_candidates.csv",
        "_metrics/2026-03-01_quality_metrics.json",
        "_sent/2026-03-01_email_sent.json",
    }


def test_main_deletes_expired_and_keeps_rest(tmp_path):
    reports = _setup_reports(tmp_path)

    rc = main([
        "--reports-dir", str(reports),
        "--reference-date", REFERENCE.isoformat(),
    ])

    assert rc == 0
    assert not (reports / "2026-01-01.md").exists()
    assert not (reports / "_candidates" / "2026-03-01_candidates.csv").exists()
    # 최근 파일·인덱스·캐시·비날짜 파일은 유지
    assert (reports / "2026-07-14.md").exists()
    assert (reports / "index.html").exists()
    assert (reports / "_cache" / "summary_cache.json").exists()
    assert (reports / "_candidates" / "2026-07-01_candidates.csv").exists()
    assert (reports / "_metrics" / "relevance_candidate_eval.json").exists()


def test_dry_run_deletes_nothing(tmp_path):
    reports = _setup_reports(tmp_path)

    rc = main([
        "--reports-dir", str(reports),
        "--reference-date", REFERENCE.isoformat(),
        "--dry-run",
    ])

    assert rc == 0
    assert (reports / "2026-01-01.md").exists()
    assert (reports / "_sent" / "2026-03-01_email_sent.json").exists()


def test_missing_reports_dir_is_noop(tmp_path):
    rc = main(["--reports-dir", str(tmp_path / "nope")])
    assert rc == 0


def test_artifact_retention_exceeds_candidate_model_lookback(tmp_path):
    # 후보 모델 학습은 최근 21일 candidates를 사용하므로,
    # 기본 보존 기간이 그보다 넉넉히 길어야 학습 데이터가 잘리지 않는다.
    from scripts.prune_reports import DEFAULT_ARTIFACT_KEEP_DAYS

    assert DEFAULT_ARTIFACT_KEEP_DAYS >= 30
