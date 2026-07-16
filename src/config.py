from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)


def env_int(
    name: str, default: int, minimum: int = 1, maximum: int | None = None
) -> int:
    """범위 검증이 있는 int 환경변수 로더. 잘못된 값은 경고 후 기본값 사용."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s value; using default %s", name, default)
        return default
    if value < minimum:
        logger.warning("Invalid %s value below %s; using default %s", name, minimum, default)
        return default
    if maximum is not None and value > maximum:
        logger.warning("Invalid %s value above %s; using default %s", name, maximum, default)
        return default
    return value


def env_float(
    name: str, default: float, minimum: float = 0.0, maximum: float | None = None
) -> float:
    """범위 검증이 있는 float 환경변수 로더. 잘못된 값은 경고 후 기본값 사용."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s value; using default %s", name, default)
        return default
    if value < minimum:
        logger.warning("Invalid %s value below %s; using default %s", name, minimum, default)
        return default
    if maximum is not None and value > maximum:
        logger.warning("Invalid %s value above %s; using default %s", name, maximum, default)
        return default
    return value


def load_dotenv_if_present(path: Path | str = ".env") -> int:
    """로컬 개발 편의용 .env 로더 (의존성 없음, best-effort).

    이미 export된 환경변수는 절대 덮어쓰지 않는다 — CI secrets가 항상 우선.
    파일이 없으면 아무것도 하지 않는다. 로드된 변수 수를 반환한다.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return 0
    loaded = 0
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def now_kst() -> datetime:
    return datetime.now(tz=KST)


@dataclass(frozen=True)
class NaverConfig:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class AppConfig:
    naver: NaverConfig


def load_config() -> AppConfig:
    # NAVER API HUB(네이버 클라우드 플랫폼) 전용 — 2026년 이관으로 기존
    # NAVER Developers Center 키(NAVER_CLIENT_ID/SECRET)는 더 이상 유효하지 않다.
    client_id = os.environ.get("NCP_APIGW_API_KEY_ID")
    client_secret = os.environ.get("NCP_APIGW_API_KEY")
    if not client_id or not client_secret:
        missing = [
            name
            for name, value in [
                ("NCP_APIGW_API_KEY_ID", client_id),
                ("NCP_APIGW_API_KEY", client_secret),
            ]
            if not value
        ]
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)} "
            "(NAVER API HUB credentials)"
        )
    return AppConfig(naver=NaverConfig(client_id=client_id, client_secret=client_secret))
