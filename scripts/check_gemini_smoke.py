"""수동 smoke 전용 — Gemini 실행 결과를 sanitized JSON으로 읽어 판정한다.

daily 파이프라인의 fail-open 동작은 **절대** 건드리지 않는다. 이 스크립트는
`.github/workflows/smoke.yml`에서만 호출되며, "Gemini 경로가 실제로 동작했는가"만
본다. 자유 형식 로그를 grep하지 않고 `run_daily`가 남긴 집계 JSON만 읽는다.

판정 규칙
- 전송 대상이 1건 이상인데 **이번 실행에서 새로 적용된 건**이 0, 내용 거부도 0
  → **실패** (API 경로가 죽었다). `gemini_applied`에는 캐시 hit이 포함되므로
  캐시 hit을 빼고 본다 — 그러지 않으면 캐시 1건 때문에 전량 실패가 통과한다.
- 내용 거부만 있고 새로 적용된 건이 0 → 보낸 기사(cache_miss)를 **전부** 내용 거부가
  설명할 때만 성공 (API는 정상 응답했고 품질 게이트가 걸러낸 것).
  일부만 거부이고 나머지가 구조 위반/누락이면 API 경로가 반쯤 죽은 것이므로 실패.
- 일부라도 새로 적용됨 → 성공
- 전송 대상이 없고 전부 캐시 hit → 성공 (호출할 이유가 없었다)
- 전송 대상이 없고 전부 본문 부족 → skip (검증한 것이 없음을 명시적으로 알린다)
- 요청 전에 기능이 꺼져 있음(키 없음/env로 끔/상한 0) → skip
- 호출을 시도한 뒤 런타임에 꺼짐(인증 실패/잘못된 모델/breaker/프로그래밍 오류)
  → **실패** (라이브 경로가 죽은 것을 초록으로 넘기지 않는다)
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

# 요청을 한 번도 보내기 전에 기능이 꺼진 이유 — 검증할 것이 없으므로 skip이다.
# 그 밖의 사유(auth / bad_model / consecutive_failures / programming_error)는
# **호출을 시도한 뒤** 런타임에 꺼진 것이므로 라이브 경로가 죽었다는 뜻 → 실패.
PRE_REQUEST_DISABLE_REASONS = frozenset(
    {"no_api_key", "disabled_by_env", "max_summaries_zero", "max_requests_zero"}
)

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
        if disabled_reason in PRE_REQUEST_DISABLE_REASONS:
            return STATUS_SKIPPED, f"gemini disabled before any request ({disabled_reason})"
        # 런타임 비활성화는 "호출해봤는데 죽었다"는 뜻이다 — 조용히 통과시키지 않는다.
        return STATUS_FAILED, f"gemini disabled mid-run ({disabled_reason})"

    targets = _count(summary, "targets")
    if targets <= 0:
        return STATUS_SKIPPED, "no display articles to summarize"

    sent = _count(summary, "sent_articles")
    applied = _count(summary, "gemini_applied")
    rejected = _count(summary, "content_rejected")
    cache_hits = _count(summary, "cache_hits")
    cache_miss = _count(summary, "cache_miss")
    skipped_no_body = _count(summary, "skipped_no_body")
    # gemini_applied는 캐시 hit까지 포함한다 — 라이브 경로 검증이므로 빼고 센다.
    newly_applied = max(0, applied - cache_hits)

    if sent <= 0:
        # 아무것도 보내지 않았다면 왜 안 보냈는지로 갈린다.
        # **캐시 hit이 대상 전부를 덮을 때만** 성공이다 — 캐시 1건 + 나머지 본문 부족을
        # "호출할 이유가 없었다"로 넘기면, 라이브 경로를 한 번도 안 거치고 초록이 된다.
        if cache_hits >= targets:
            return STATUS_OK, "every target served from cache"
        if skipped_no_body >= targets:
            return STATUS_SKIPPED, "every target lacked a usable body; API path not exercised"
        return STATUS_SKIPPED, "no request was sent; API path not exercised"

    if newly_applied > 0:
        return STATUS_OK, "gemini summaries applied"

    # 여기부터는 보냈는데 이번 실행에서 새로 적용된 건이 0인 경우다.
    # 내용 거부가 보낸 기사 전부를 설명할 때만 "API는 정상, 게이트가 걸렀다"로 본다.
    # (거부 1건 + 나머지 구조 위반/누락은 API 경로가 반쯤 죽은 것이다)
    covered = newly_applied + rejected
    required = cache_miss if cache_miss > 0 else sent
    if rejected > 0 and _count(summary, "api_errors") == 0 and covered >= required:
        return STATUS_OK, "api responded; all sent articles failed the content gate"

    if rejected > 0:
        return STATUS_FAILED, (
            "sent articles to the API but nothing new was applied and "
            "content rejections do not account for every sent article"
        )
    return STATUS_FAILED, "sent articles to the API but nothing new was applied"


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
