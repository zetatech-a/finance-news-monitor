"""수동 smoke 전용 — Gemini 실행 결과를 sanitized JSON으로 읽어 판정한다.

daily 파이프라인의 fail-open 동작은 **절대** 건드리지 않는다. 이 스크립트는
`.github/workflows/smoke.yml`에서만 호출되며, "Gemini 경로가 실제로 동작했는가"만
본다. 자유 형식 로그를 grep하지 않고 `run_daily`가 남긴 집계 JSON만 읽는다.

판정 규칙
- 전송 대상이 1건 이상인데 적용도 0, 내용 거부도 0 → **실패** (API 경로가 죽었다)
- 내용 거부만 있고 적용이 0 → 성공 (API는 정상 응답했고 품질 게이트가 걸러낸 것)
- 일부라도 적용됨 → 성공
- 전송 대상이 없고 전부 캐시 hit → 성공 (호출할 이유가 없었다)
- 전송 대상이 없고 전부 본문 부족 → skip (검증한 것이 없음을 명시적으로 알린다)
- Gemini 기능 자체가 꺼져 있음 → skip
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNREADABLE = 2

# 판정에 쓰는 숫자 필드 — 이 외의 값은 읽지 않는다.
_COUNT_FIELDS = (
    "targets",
    "cache_hits",
    "cache_miss",
    "skipped_no_body",
    "sent_articles",
    "gemini_applied",
    "content_rejected",
    "items_rejected",
    "api_errors",
    "rate_limit_hits",
    "requests",
)


def _count(summary: dict[str, Any], key: str) -> int:
    value = summary.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def evaluate(summary: dict[str, Any]) -> tuple[str, str]:
    """(status, reason) — reason은 사람이 읽는 짧은 사유(기사 정보 없음)."""
    disabled_reason = summary.get("disabled_reason")
    if disabled_reason:
        return STATUS_SKIPPED, f"gemini disabled ({disabled_reason})"

    targets = _count(summary, "targets")
    if targets <= 0:
        return STATUS_SKIPPED, "no display articles to summarize"

    sent = _count(summary, "sent_articles")
    applied = _count(summary, "gemini_applied")
    rejected = _count(summary, "content_rejected")
    cache_hits = _count(summary, "cache_hits")
    skipped_no_body = _count(summary, "skipped_no_body")

    if sent <= 0:
        # 아무것도 보내지 않았다면 왜 안 보냈는지로 갈린다.
        if cache_hits >= targets:
            return STATUS_OK, "every target served from cache"
        if skipped_no_body >= targets:
            return STATUS_SKIPPED, "every target lacked a usable body; API path not exercised"
        if cache_hits > 0:
            return STATUS_OK, "no article needed an API call"
        return STATUS_SKIPPED, "nothing was sent to the API"

    if applied > 0:
        return STATUS_OK, "gemini summaries applied"

    # 여기부터는 보냈는데 적용이 0인 경우다.
    if rejected > 0 and _count(summary, "api_errors") == 0:
        # 모델이 정상 응답했고 품질 게이트가 전부 걸러낸 것 — API 경로는 살아 있다.
        return STATUS_OK, "api responded; all sent articles failed the content gate"

    return STATUS_FAILED, "sent articles to the API but nothing was applied"


def format_counts(summary: dict[str, Any]) -> str:
    """판정 근거만 한 줄로 — 제목·본문·URL·프롬프트는 애초에 JSON에 없다."""
    parts = [f"{key}={_count(summary, key)}" for key in _COUNT_FIELDS]
    model = summary.get("model")
    if isinstance(model, str) and model:
        parts.insert(0, f"model={model}")
    breaker = summary.get("breaker_tripped")
    if isinstance(breaker, bool):
        parts.append(f"breaker_tripped={breaker}")
    return " ".join(parts)


def load_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("run summary is not an object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="요약 파일이 없어도 실패로 보지 않는다(파이프라인이 그 전에 죽은 경우).",
    )
    args = parser.parse_args(argv)

    if not args.summary.exists():
        print(f"status={STATUS_SKIPPED} reason=run summary not found at {args.summary}")
        return EXIT_OK if args.allow_missing else EXIT_UNREADABLE

    try:
        summary = load_summary(args.summary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"status={STATUS_FAILED} reason=unreadable run summary ({type(exc).__name__})")
        return EXIT_UNREADABLE

    status, reason = evaluate(summary)
    print(f"status={status} reason={reason}")
    print(format_counts(summary))

    if status == STATUS_FAILED:
        print(f"::error::Gemini smoke check failed — {reason}")
        return EXIT_FAILED
    if status == STATUS_SKIPPED:
        print(f"::warning::Gemini path not verified — {reason}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
