from __future__ import annotations

import logging
import smtplib
from pathlib import Path

import pytest

from src import notify_email


def _set_required_mail_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASS", "smtp-secret")
    monkeypatch.setenv("MAIL_FROM", "bot@example.com")
    monkeypatch.setenv("MAIL_TO", "one@example.com,two@example.com")


class FakeSMTP:
    attempts = 0
    init_args: list[dict] = []
    fail_until = 0
    init_fail_until = 0
    refused: dict = {}

    def __init__(self, host, port, timeout=None):
        type(self).attempts += 1
        type(self).init_args.append({"host": host, "port": port, "timeout": timeout})
        if type(self).attempts <= type(self).init_fail_until:
            raise ConnectionRefusedError("temporary refusal")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, user, password):
        return None

    def send_message(self, message, to_addrs=None):
        if type(self).attempts <= type(self).fail_until:
            raise smtplib.SMTPServerDisconnected("transient disconnect")
        type(self).sent_message = message
        type(self).sent_to_addrs = list(to_addrs or [])
        return dict(type(self).refused)


@pytest.fixture(autouse=True)
def reset_fake_smtp():
    FakeSMTP.attempts = 0
    FakeSMTP.init_args = []
    FakeSMTP.fail_until = 0
    FakeSMTP.init_fail_until = 0
    FakeSMTP.refused = {}
    FakeSMTP.sent_message = None
    FakeSMTP.sent_to_addrs = None


def test_retries_transient_smtp_failure_then_succeeds(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 1
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")

    notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.attempts == 2
    assert FakeSMTP.sent_message["Subject"] == "subject"


def test_retries_connection_refused_then_succeeds(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.init_fail_until = 1
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")

    notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.attempts == 2
    assert FakeSMTP.sent_message["Subject"] == "subject"


def test_single_send_hides_recipient_list_from_headers(monkeypatch):
    _set_required_mail_env(monkeypatch)
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)

    notify_email.send_email("subject", "body", attachments=[])

    # 모든 수신자에게 한 번의 SMTP 트랜잭션으로 발송 (부분 발송 상태가 없어야 함)
    assert FakeSMTP.attempts == 1
    assert FakeSMTP.sent_to_addrs == ["one@example.com", "two@example.com"]
    # 수신자 목록은 envelope로만 전달되고 어떤 헤더에도 노출되지 않아야 함
    headers = str(FakeSMTP.sent_message)
    assert "one@example.com" not in headers
    assert "two@example.com" not in headers


def test_refused_recipients_raise_and_retry(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.refused = {"two@example.com": (550, b"user unknown")}
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")

    with pytest.raises(RuntimeError, match="Email send failed after 3 attempts"):
        notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.attempts == 3


def test_fails_after_max_retry_attempts(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 3
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")

    with pytest.raises(RuntimeError, match="Email send failed after 3 attempts"):
        notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.attempts == 3


def test_does_not_retry_missing_config(monkeypatch):
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)

    with pytest.raises(RuntimeError, match="Missing environment variable: SMTP_HOST"):
        notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.attempts == 0


def test_uses_configured_smtp_timeout(monkeypatch):
    _set_required_mail_env(monkeypatch)
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "7.5")

    notify_email.send_email("subject", "body", attachments=[])

    assert FakeSMTP.init_args[0]["timeout"] == 7.5


def test_unsafe_env_overrides_fall_back_to_bounded_defaults(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 3
    sleeps: list[float] = []
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", sleeps.append)
    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "999")
    monkeypatch.setenv("MAIL_RETRY_ATTEMPTS", "999")
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "999")

    with pytest.raises(RuntimeError, match="Email send failed after 3 attempts"):
        notify_email.send_email("subject", "body", attachments=[])

    assert [call["timeout"] for call in FakeSMTP.init_args] == [30.0, 30.0, 30.0]
    assert sleeps == [10.0, 20.0]


def test_mail_backoff_sleep_is_capped(monkeypatch):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 5
    sleeps: list[float] = []
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", sleeps.append)
    monkeypatch.setenv("MAIL_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "120")

    with pytest.raises(RuntimeError, match="Email send failed after 5 attempts"):
        notify_email.send_email("subject", "body", attachments=[])

    assert sleeps == [120.0, 120.0, 120.0, 120.0]


def test_email_failure_does_not_create_or_touch_sent_marker(monkeypatch, tmp_path):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 3
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.chdir(tmp_path)
    Path("reports").mkdir()

    with pytest.raises(RuntimeError):
        notify_email.main()

    assert not (tmp_path / "reports" / "_sent").exists()


def test_does_not_log_smtp_password(monkeypatch, caplog):
    _set_required_mail_env(monkeypatch)
    FakeSMTP.fail_until = 1
    monkeypatch.setattr(notify_email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify_email.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("MAIL_RETRY_BACKOFF_SECONDS", "0")

    with caplog.at_level(logging.WARNING):
        notify_email.send_email("subject", "body", attachments=[])

    assert "smtp-secret" not in caplog.text
    assert "SMTPServerDisconnected" in caplog.text
