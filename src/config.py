from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


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
    client_id = os.environ.get("NAVER_CLIENT_ID")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        missing = [
            name
            for name, value in [
                ("NAVER_CLIENT_ID", client_id),
                ("NAVER_CLIENT_SECRET", client_secret),
            ]
            if not value
        ]
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return AppConfig(naver=NaverConfig(client_id=client_id, client_secret=client_secret))
