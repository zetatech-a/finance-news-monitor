from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_cache(path: Path) -> dict[str, str]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 값이 문자열인 것만 유지
            return {str(k): str(v) for k, v in data.items() if v is not None}
    except Exception:
        pass
    return {}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 너무 커지는 걸 방지하려면 최근 N개만 유지(기본 5000)
    MAX_ITEMS = 5000
    if len(cache) > MAX_ITEMS:
        # dict는 삽입순서 유지(파이썬 3.7+)라서 마지막 MAX_ITEMS만 유지
        items = list(cache.items())[-MAX_ITEMS:]
        cache = dict(items)

    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
