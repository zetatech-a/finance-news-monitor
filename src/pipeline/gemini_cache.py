"""Gemini 3줄 요약 전용 캐시.

기존 추출요약 캐시(`summary_cache.py`, 평면 {url: str})와 **파일도 스키마도 분리**한다.
- 기존 캐시는 이미 상한(5000)에 도달해 있어, 같은 파일에 얹으면 추출요약 항목 축출이
  빨라져 기존 기능이 열화된다.
- Gemini 캐시는 canonical URL + model + prompt version + schema version으로 키를 만들어
  모델·프롬프트·스키마가 바뀌면 자동으로 cache miss가 나야 한다.

기사 전문은 저장하지 않는다 — 파생된 3줄만 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
# 하루 최대 300건까지 요약하므로 ~33일치를 담는다. 항목당 ~400바이트라 파일은 4MB 수준이고,
# 이 파일은 매일 커밋되므로 무한정 키우지 않는다.
MAX_ITEMS = 10_000


def cache_key(url: str, model: str, prompt_version: int, schema_version: int) -> str:
    raw = f"{(url or '').strip()}|{(model or '').strip()}|p{prompt_version}|s{schema_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def load_gemini_cache(path: Path) -> dict[str, dict[str, Any]]:
    """손상된 파일/항목이 있어도 절대 raise하지 않는다 — 최악의 경우 빈 캐시."""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("Gemini cache unreadable (%s); starting empty", type(exc).__name__)
        return {}

    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}

    # 항목 단위로 검증 — 손상된 항목 하나가 전체 로딩을 깨뜨리지 않게 한다.
    clean: dict[str, dict[str, Any]] = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        lines = entry.get("lines")
        if not isinstance(lines, list) or len(lines) != 3:
            continue
        if not all(isinstance(line, str) and line.strip() for line in lines):
            continue
        clean[key] = entry
    return clean


def get_cached_lines(cache: dict[str, dict[str, Any]], key: str) -> list[str] | None:
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    lines = entry.get("lines")
    if not isinstance(lines, list) or len(lines) != 3:
        return None
    if not all(isinstance(line, str) and line.strip() for line in lines):
        return None
    return [line.strip() for line in lines]


def put_cached_lines(
    cache: dict[str, dict[str, Any]],
    key: str,
    *,
    url: str,
    model: str,
    prompt_version: int,
    schema_version: int,
    lines: list[str],
    created_at: str,
) -> None:
    """본문·프롬프트·응답 원문은 저장하지 않는다."""
    cache[key] = {
        "url": url,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "lines": list(lines),
        "created_at": created_at,
    }


def save_gemini_cache(path: Path, cache: dict[str, dict[str, Any]]) -> None:
    """tmp 파일에 쓴 뒤 os.replace로 교체 — 중단돼도 반쪽 파일이 남지 않는다."""
    trimmed = cache
    if len(cache) > MAX_ITEMS:
        # dict는 삽입순서를 유지하므로 최근 MAX_ITEMS개만 남긴다(기존 캐시와 동일 정책).
        trimmed = dict(list(cache.items())[-MAX_ITEMS:])

    payload = {"version": CACHE_VERSION, "entries": trimmed}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp_path, path)
    except OSError as exc:
        # 캐시 저장 실패가 리포트 생성을 막지 않는다.
        logger.warning("Failed to save Gemini cache: %s", type(exc).__name__)
