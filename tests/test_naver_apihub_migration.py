from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from src.config import NaverConfig, load_config
from src.fetchers import naver
from tests.test_naver_retry import FakeResponse, FakeSession, _success_payload


def _window() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
    )


def _clear_naver_env(monkeypatch):
    for name in (
        "NCP_APIGW_API_KEY_ID",
        "NCP_APIGW_API_KEY",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# load_config: NAVER API HUB 키 전용 (이관 후 기존 키는 무효)
# ---------------------------------------------------------------------------

def test_load_config_uses_apihub_keys(monkeypatch):
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NCP_APIGW_API_KEY_ID", "hub-id")
    monkeypatch.setenv("NCP_APIGW_API_KEY", "hub-secret")

    config = load_config()

    assert config.naver.client_id == "hub-id"
    assert config.naver.client_secret == "hub-secret"


def test_load_config_ignores_legacy_keys(monkeypatch):
    # 이관 후 무효화된 기존 키만 있으면 명확히 실패해야 한다
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NAVER_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "legacy-secret")

    with pytest.raises(EnvironmentError, match="NCP_APIGW_API_KEY_ID"):
        load_config()


def test_load_config_error_lists_missing_apihub_vars(monkeypatch):
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NCP_APIGW_API_KEY_ID", "hub-id")

    with pytest.raises(EnvironmentError, match="NCP_APIGW_API_KEY"):
        load_config()


# ---------------------------------------------------------------------------
# fetch_news: API HUB 엔드포인트/인증 헤더
# ---------------------------------------------------------------------------

def test_fetch_news_uses_apihub_endpoint_and_headers(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    config = NaverConfig(client_id="hub-id", client_secret="hub-secret")

    items = naver.fetch_news(config, ["금융"], start, end, display=100, max_pages=1)

    call = session.calls[0]
    assert call["url"] == "https://naverapihub.apigw.ntruss.com/search/v1/news"
    assert call["headers"] == {
        "X-NCP-APIGW-API-KEY-ID": "hub-id",
        "X-NCP-APIGW-API-KEY": "hub-secret",
    }
    # 요청 파라미터는 기존과 동일 (이관 가이드 기준)
    assert call["params"]["query"] == "금융"
    assert call["params"]["sort"] == "date"
    assert len(items) == 1


def test_apihub_secret_not_logged_on_retry(monkeypatch, caplog):
    start, end = _window()
    session = FakeSession([FakeResponse(500), FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")
    config = NaverConfig(client_id="hub-id", client_secret="hub-super-secret")

    with caplog.at_level(logging.INFO):
        naver.fetch_news(config, ["금융"], start, end, display=100, max_pages=1)

    assert "hub-super-secret" not in caplog.text
