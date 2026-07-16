from __future__ import annotations

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
# load_config: 신규 API HUB 키 우선, 레거시 폴백
# ---------------------------------------------------------------------------

def test_load_config_prefers_apihub_keys(monkeypatch):
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NCP_APIGW_API_KEY_ID", "hub-id")
    monkeypatch.setenv("NCP_APIGW_API_KEY", "hub-secret")
    monkeypatch.setenv("NAVER_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "legacy-secret")

    config = load_config()

    assert config.naver.api_mode == "apihub"
    assert config.naver.client_id == "hub-id"
    assert config.naver.client_secret == "hub-secret"


def test_load_config_falls_back_to_legacy_keys(monkeypatch):
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NAVER_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "legacy-secret")

    config = load_config()

    assert config.naver.api_mode == "openapi"
    assert config.naver.client_id == "legacy-id"


def test_load_config_partial_apihub_keys_fall_back_to_legacy(monkeypatch):
    # KEY_ID만 있고 KEY가 없으면 API HUB 모드로 가면 안 됨
    _clear_naver_env(monkeypatch)
    monkeypatch.setenv("NCP_APIGW_API_KEY_ID", "hub-id")
    monkeypatch.setenv("NAVER_CLIENT_ID", "legacy-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "legacy-secret")

    config = load_config()

    assert config.naver.api_mode == "openapi"


def test_load_config_error_mentions_both_credential_sets(monkeypatch):
    _clear_naver_env(monkeypatch)

    with pytest.raises(EnvironmentError, match="NCP_APIGW_API_KEY_ID"):
        load_config()


# ---------------------------------------------------------------------------
# fetch_news: 모드별 엔드포인트/인증 헤더
# ---------------------------------------------------------------------------

def test_apihub_mode_uses_new_endpoint_and_headers(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    config = NaverConfig(client_id="hub-id", client_secret="hub-secret", api_mode="apihub")

    items = naver.fetch_news(config, ["금융"], start, end, display=100, max_pages=1)

    call = session.calls[0]
    assert call["url"] == "https://naverapihub.apigw.ntruss.com/search/v1/news"
    assert call["headers"] == {
        "X-NCP-APIGW-API-KEY-ID": "hub-id",
        "X-NCP-APIGW-API-KEY": "hub-secret",
    }
    # 요청 파라미터는 기존과 동일해야 함 (이관 가이드 기준)
    assert call["params"]["query"] == "금융"
    assert call["params"]["sort"] == "date"
    assert len(items) == 1


def test_legacy_mode_keeps_old_endpoint_and_headers(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    config = NaverConfig(client_id="legacy-id", client_secret="legacy-secret")

    naver.fetch_news(config, ["금융"], start, end, display=100, max_pages=1)

    call = session.calls[0]
    assert call["url"] == "https://openapi.naver.com/v1/search/news.json"
    assert call["headers"] == {
        "X-Naver-Client-Id": "legacy-id",
        "X-Naver-Client-Secret": "legacy-secret",
    }


def test_apihub_secret_not_logged_on_retry(monkeypatch, caplog):
    import logging

    start, end = _window()
    session = FakeSession([FakeResponse(500), FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")
    config = NaverConfig(client_id="hub-id", client_secret="hub-super-secret", api_mode="apihub")

    with caplog.at_level(logging.INFO):
        naver.fetch_news(config, ["금융"], start, end, display=100, max_pages=1)

    assert "hub-super-secret" not in caplog.text
