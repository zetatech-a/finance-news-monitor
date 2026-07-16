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
    # "apihub": NAVER API HUB(naverapihub.apigw.ntruss.com, X-NCP-APIGW-* 헤더)
    # "openapi": 기존 NAVER Developers Center(openapi.naver.com, X-Naver-Client-* 헤더)
    api_mode: str = "openapi"


@dataclass(frozen=True)
class AppConfig:
    naver: NaverConfig


def load_config() -> AppConfig:
    # NAVER API HUB 이관: 신규 키(NCP_APIGW_*)가 설정되어 있으면 API HUB 모드를
    # 우선 사용하고, 없으면 기존 NAVER Developers Center 키로 폴백한다.
    # 신규 키에 문제가 생기면 해당 secret만 제거해 즉시 기존 방식으로 롤백할 수 있다.
    apihub_key_id = os.environ.get("NCP_APIGW_API_KEY_ID")
    apihub_key = os.environ.get("NCP_APIGW_API_KEY")
    if apihub_key_id and apihub_key:
        return AppConfig(
            naver=NaverConfig(
                client_id=apihub_key_id,
                client_secret=apihub_key,
                api_mode="apihub",
            )
        )

    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise EnvironmentError(
            "Missing Naver API credentials: set NCP_APIGW_API_KEY_ID + "
            "NCP_APIGW_API_KEY (NAVER API HUB) or NAVER_CLIENT_ID + "
            "NAVER_CLIENT_SECRET (legacy NAVER Developers Center)"
        )
    return AppConfig(
        naver=NaverConfig(
            client_id=client_id, client_secret=client_secret, api_mode="openapi"
        )
    )
