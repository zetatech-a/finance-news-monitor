from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
import requests

from src.config import NaverConfig
from src.fetchers import naver


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {"items": []}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, *, headers, params, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout}
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _config() -> NaverConfig:
    return NaverConfig(client_id="client-id", client_secret="super-secret")


def _window() -> tuple[datetime, datetime]:
    return (
        datetime(2026, 5, 13, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
    )


def _success_payload() -> dict:
    return {
        "items": [
            {
                "title": "<b>title</b>",
                "description": "desc",
                "link": "https://news.naver.com/a",
                "originallink": "https://example.com/a",
                "pubDate": "Wed, 13 May 2026 09:00:00 +0900",
            }
        ]
    }


def test_naive_pubdate_is_treated_as_kst_and_does_not_crash(monkeypatch):
    start, end = _window()
    payload = {
        "items": [
            {
                "title": "tz 없는 기사",
                "description": "desc",
                "link": "https://news.naver.com/naive",
                "originallink": "https://example.com/naive",
                "pubDate": "2026-05-13 09:00:00",  # 타임존 정보 없음
            }
        ]
    }
    session = FakeSession([FakeResponse(200, payload)])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)

    # naive datetime이 그대로 흘러가면 윈도우 비교에서 TypeError가 났었다
    items = naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(items) == 1
    pub = items[0]["pubDate"]
    assert pub.tzinfo is not None
    assert pub.utcoffset().total_seconds() == 9 * 3600  # KST로 간주


def test_retries_once_on_http_500_then_succeeds(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(500), FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    items = naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 2
    assert [item["title"] for item in items] == ["title"]


def test_retries_on_http_429_then_succeeds(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(429), FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    items = naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 2
    assert len(items) == 1


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_does_not_retry_permanent_or_auth_failures(monkeypatch, status_code):
    start, end = _window()
    session = FakeSession([FakeResponse(status_code)])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)

    with pytest.raises(requests.HTTPError):
        naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 1


def test_raises_clear_error_after_max_retry_attempts(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    with pytest.raises(RuntimeError, match="Naver request failed after 3 attempts with HTTP 503"):
        naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 3


def test_retries_connection_error_then_succeeds(monkeypatch):
    start, end = _window()
    session = FakeSession(
        [requests.exceptions.ConnectionError("temporary"), FakeResponse(200, _success_payload())]
    )
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    items = naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 2
    assert len(items) == 1


def test_retries_timeout_then_succeeds(monkeypatch):
    start, end = _window()
    session = FakeSession(
        [requests.exceptions.Timeout("temporary"), FakeResponse(200, _success_payload())]
    )
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    items = naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert len(session.calls) == 2
    assert len(items) == 1


def test_uses_configured_timeout(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setenv("NAVER_HTTP_TIMEOUT_SECONDS", "2.5")

    naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert session.calls[0]["timeout"] == 2.5


def test_unsafe_env_overrides_fall_back_to_bounded_defaults(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    sleeps: list[float] = []
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", sleeps.append)
    monkeypatch.setenv("NAVER_HTTP_TIMEOUT_SECONDS", "999")
    monkeypatch.setenv("NAVER_RETRY_ATTEMPTS", "999")
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "999")

    with pytest.raises(RuntimeError, match="Naver request failed after 3 attempts"):
        naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert [call["timeout"] for call in session.calls] == [10.0, 10.0, 10.0]
    assert sleeps == [1.0, 2.0]


def test_backoff_sleep_is_capped(monkeypatch):
    start, end = _window()
    session = FakeSession([FakeResponse(503)] * 5)
    sleeps: list[float] = []
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", sleeps.append)
    monkeypatch.setenv("NAVER_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "30")

    with pytest.raises(RuntimeError, match="Naver request failed after 5 attempts"):
        naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert sleeps == [30.0, 30.0, 30.0, 30.0]


def test_does_not_log_naver_client_secret(monkeypatch, caplog):
    start, end = _window()
    session = FakeSession([FakeResponse(500), FakeResponse(200, _success_payload())])
    monkeypatch.setattr(naver.requests, "Session", lambda: session)
    monkeypatch.setattr(naver.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("NAVER_RETRY_BACKOFF_SECONDS", "0")

    with caplog.at_level(logging.WARNING):
        naver.fetch_news(_config(), ["금융"], start, end, display=100, max_pages=1)

    assert "super-secret" not in caplog.text
    assert "500" in caplog.text
