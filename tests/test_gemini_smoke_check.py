"""smoke 전용 strict 검증 — daily fail-open은 건드리지 않는다."""
from __future__ import annotations

import json

import pytest

from scripts.check_gemini_smoke import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNREADABLE,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    evaluate,
    format_counts,
    main,
)


def _summary(**overrides):
    base = {
        "model": "gemini-3.6-flash",
        "targets": 0,
        "cache_hits": 0,
        "cache_miss": 0,
        "skipped_no_body": 0,
        "sent_articles": 0,
        "gemini_applied": 0,
        "content_rejected": 0,
        "items_rejected": 0,
        "api_errors": 0,
        "rate_limit_hits": 0,
        "requests": 0,
        "breaker_tripped": False,
        "disabled_reason": None,
    }
    base.update(overrides)
    return base


def _run(tmp_path, summary, *args):
    path = tmp_path / "gemini-run-summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return main(["--summary", str(path), *args])


# --- 실패해야 하는 경우 ------------------------------------------------------


def test_total_api_failure_with_nothing_applied_fails(tmp_path, capsys):
    """실제로 겪은 503 반복 상황 — 50건 보냈는데 하나도 적용 못 함."""
    summary = _summary(
        targets=50, cache_miss=50, sent_articles=50, requests=4, api_errors=4
    )
    assert evaluate(summary)[0] == STATUS_FAILED
    assert _run(tmp_path, summary) == EXIT_FAILED
    out = capsys.readouterr().out
    assert "status=failed" in out
    assert "::error::" in out


def test_sent_but_nothing_applied_and_no_rejection_fails(tmp_path):
    """내용 거부도 없이 적용 0이면 API 경로가 죽은 것이다."""
    summary = _summary(targets=25, cache_miss=25, sent_articles=25, requests=1)
    assert _run(tmp_path, summary) == EXIT_FAILED


def test_applied_zero_with_rejections_but_api_errors_fails(tmp_path):
    """일부는 거부, 나머지는 API 오류 — 적용이 0이면 성공으로 볼 수 없다."""
    summary = _summary(
        targets=25, cache_miss=25, sent_articles=25, content_rejected=5, api_errors=2
    )
    assert _run(tmp_path, summary) == EXIT_FAILED


# --- 성공해야 하는 경우 ------------------------------------------------------


def test_real_smoke_result_passes(tmp_path, capsys):
    """실제 gemini-3.6-flash 50건 결과."""
    summary = _summary(
        targets=50,
        cache_hits=1,
        cache_miss=49,
        skipped_no_body=1,
        sent_articles=49,
        requests=2,
        gemini_applied=40,
        content_rejected=9,
    )
    assert evaluate(summary)[0] == STATUS_OK
    assert _run(tmp_path, summary) == EXIT_OK
    assert "::error::" not in capsys.readouterr().out


def test_content_rejection_only_is_not_a_failure(tmp_path, capsys):
    """API는 200으로 정상 응답했고 품질 게이트가 전부 걸러낸 경우."""
    summary = _summary(
        targets=30, cache_miss=30, sent_articles=30, requests=2, content_rejected=30
    )
    status, reason = evaluate(summary)
    assert status == STATUS_OK
    assert "content gate" in reason
    assert _run(tmp_path, summary) == EXIT_OK
    assert "::error::" not in capsys.readouterr().out


def test_partial_application_passes(tmp_path):
    summary = _summary(
        targets=25, cache_miss=25, sent_articles=25, requests=1, gemini_applied=1,
        content_rejected=0, api_errors=1,
    )
    assert _run(tmp_path, summary) == EXIT_OK


def test_every_target_from_cache_passes(tmp_path, capsys):
    summary = _summary(targets=50, cache_hits=50, gemini_applied=50)
    status, reason = evaluate(summary)
    assert status == STATUS_OK
    assert "cache" in reason
    assert _run(tmp_path, summary) == EXIT_OK
    assert "::error::" not in capsys.readouterr().out


def test_applied_and_rejected_cover_everything_sent_passes(tmp_path):
    summary = _summary(
        targets=20, cache_miss=20, sent_articles=20, requests=1,
        gemini_applied=15, content_rejected=5,
    )
    assert _run(tmp_path, summary) == EXIT_OK


# --- 명시적 skip -------------------------------------------------------------


def test_all_targets_without_a_body_is_an_explicit_skip(tmp_path, capsys):
    """API를 아예 안 불렀으므로 검증한 것이 없다 — 조용히 통과시키지 않는다."""
    summary = _summary(targets=40, cache_miss=40, skipped_no_body=40)
    status, reason = evaluate(summary)
    assert status == STATUS_SKIPPED
    assert "API path not exercised" in reason
    assert _run(tmp_path, summary) == EXIT_OK
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "::error::" not in out


@pytest.mark.parametrize("reason", ["no_api_key", "disabled_by_env", "max_summaries_zero"])
def test_disabled_feature_is_a_skip_not_a_failure(tmp_path, reason):
    summary = _summary(disabled_reason=reason)
    assert evaluate(summary)[0] == STATUS_SKIPPED
    assert _run(tmp_path, summary) == EXIT_OK


def test_no_display_articles_is_a_skip(tmp_path):
    assert _run(tmp_path, _summary(targets=0)) == EXIT_OK


# --- 파일 문제 ---------------------------------------------------------------


def test_missing_summary_is_unreadable_unless_allowed(tmp_path):
    missing = str(tmp_path / "nope.json")
    assert main(["--summary", missing]) == EXIT_UNREADABLE
    assert main(["--summary", missing, "--allow-missing"]) == EXIT_OK


def test_corrupt_summary_is_reported(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert main(["--summary", str(path)]) == EXIT_UNREADABLE


# --- 출력 안전성 -------------------------------------------------------------


def test_output_contains_only_counts_and_model(tmp_path, capsys):
    summary = _summary(
        targets=5, cache_miss=5, sent_articles=5, gemini_applied=5, requests=1
    )
    # run_daily가 만드는 JSON에는 애초에 이런 값이 없지만, 들어와도 출력하지 않는다.
    summary["secret"] = "AIzaSyFAKEKEYDONOTUSE1234567890"
    summary["title"] = "대통령 지지율 52%"
    summary["url"] = "https://n.news.naver.com/article/123"

    _run(tmp_path, summary)
    out = capsys.readouterr().out
    assert "AIzaSy" not in out
    assert "대통령 지지율" not in out
    assert "n.news.naver.com" not in out
    assert "model=gemini-3.6-flash" in out
    assert "gemini_applied=5" in out


def test_format_counts_ignores_non_integer_values():
    line = format_counts(_summary(targets="많음", gemini_applied=True, sent_articles=3))
    assert "targets=0" in line  # 숫자가 아니면 0으로 떨어뜨린다
    assert "gemini_applied=0" in line  # bool은 카운트로 보지 않는다
    assert "sent_articles=3" in line
