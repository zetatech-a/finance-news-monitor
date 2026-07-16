from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from src.config import now_kst

DEFAULT_SMTP_TIMEOUT_SECONDS = 30.0
DEFAULT_MAIL_RETRY_ATTEMPTS = 3
DEFAULT_MAIL_RETRY_BACKOFF_SECONDS = 10.0
MAX_SMTP_TIMEOUT_SECONDS = 120.0
MAX_MAIL_RETRY_ATTEMPTS = 5
MAX_MAIL_RETRY_BACKOFF_SECONDS = 120.0

logger = logging.getLogger(__name__)


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def _env_int(
    name: str, default: int, minimum: int = 1, maximum: int | None = None
) -> int:
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


def _env_float(
    name: str, default: float, minimum: float = 0.0, maximum: float | None = None
) -> float:
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


def _backoff_seconds(initial_backoff: float, attempt: int) -> float:
    return min(initial_backoff * (2 ** (attempt - 1)), MAX_MAIL_RETRY_BACKOFF_SECONDS)


def _send_message_once(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    message: EmailMessage,
    recipients: list[str],
    timeout: float,
) -> None:
    # STARTTLS (587)
    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        server.starttls()
        server.login(user, password)
        # smtplib은 일부 수신자만 거부돼도 예외 없이 dict로 반환하므로 직접 실패 처리한다.
        refused = server.send_message(message, to_addrs=recipients)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)


def _send_message_with_retry(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    message: EmailMessage,
    recipients: list[str],
    timeout: float,
    attempts: int,
    backoff: float,
) -> None:
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            _send_message_once(
                host=host,
                port=port,
                user=user,
                password=password,
                message=message,
                recipients=recipients,
                timeout=timeout,
            )
            return
        except (smtplib.SMTPException, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            wait = _backoff_seconds(backoff, attempt)
            logger.warning(
                "Email send retry %s/%s after %s; waiting %.1fs",
                attempt,
                attempts,
                exc.__class__.__name__,
                wait,
            )
            time.sleep(wait)

    error_name = last_error.__class__.__name__ if last_error else "unknown error"
    logger.error("Email send failed after %s attempts: %s", attempts, error_name)
    raise RuntimeError(f"Email send failed after {attempts} attempts: {error_name}") from last_error


def send_email(subject: str, body: str, attachments: list[Path]) -> None:
    host = _req("SMTP_HOST")
    port = int(_req("SMTP_PORT"))
    user = _req("SMTP_USER")
    password = _req("SMTP_PASS")

    # Keep MAIL_FROM as a pure email address for maximum SMTP compatibility.
    mail_from_email = _req("MAIL_FROM")
    mail_from_name = os.environ.get("MAIL_FROM_NAME", "").strip() or "금융동향봇"

    mail_to = [x.strip() for x in _req("MAIL_TO").split(",") if x.strip()]

    timeout = _env_float(
        "SMTP_TIMEOUT_SECONDS",
        DEFAULT_SMTP_TIMEOUT_SECONDS,
        minimum=0.1,
        maximum=MAX_SMTP_TIMEOUT_SECONDS,
    )
    attempts = _env_int(
        "MAIL_RETRY_ATTEMPTS",
        DEFAULT_MAIL_RETRY_ATTEMPTS,
        maximum=MAX_MAIL_RETRY_ATTEMPTS,
    )
    backoff = _env_float(
        "MAIL_RETRY_BACKOFF_SECONDS",
        DEFAULT_MAIL_RETRY_BACKOFF_SECONDS,
        maximum=MAX_MAIL_RETRY_BACKOFF_SECONDS,
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((mail_from_name, mail_from_email))
    # 수신자 목록 노출 방지: To 헤더에는 발신 주소만 넣고, 실제 수신자는
    # SMTP envelope(to_addrs)로만 전달한다(BCC와 동일). 단일 트랜잭션이라
    # 수신자별 개별 발송과 달리 일부만 발송된 상태로 실패하지 않는다.
    msg["To"] = formataddr((mail_from_name, mail_from_email))
    msg.set_content(body)

    for path in attachments:
        if not path.exists():
            continue
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    _send_message_with_retry(
        host=host,
        port=port,
        user=user,
        password=password,
        message=msg,
        recipients=mail_to,
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
    )


def resolve_report_date_and_attachments(report_dir: Path) -> tuple[str, list[Path]]:
    """
    Return (report_date, attachments) for email.
    Email attachments policy: HTML only, 당일 리포트만.

    과거에는 당일 리포트가 없으면 가장 최근 옛 리포트로 대체 발송했지만,
    생성 실패가 '옛 날짜 리포트 메일'로 위장되는 문제가 있어 제거했다.
    당일 파일이 없으면 main()에서 발송 없이 실패하고, sent marker가 없으므로
    다음 cron 실행이 리포트를 다시 만든 뒤 발송을 재시도한다.
    """
    today = now_kst().date().isoformat()
    return today, [report_dir / f"{today}.html"]


def main() -> None:
    report_dir = Path("reports")
    report_date, attachments = resolve_report_date_and_attachments(report_dir)

    missing = [path for path in attachments if not path.exists()]
    if missing:
        raise RuntimeError(
            f"오늘({report_date}) 리포트가 없어 이메일을 발송하지 않습니다: "
            f"{missing[0]} — 리포트 생성부터 다시 실행하세요"
        )

    subject = f"[금융권 언론동향] {report_date} (KST)"

    # Keep body short (spam-safe) and user-oriented. Avoid internal implementation details.
    body = (
        "금융권 일일 언론동향 리포트입니다.\n"
        "대부 관련 이슈를 우선 반영했습니다.\n"
        f"- 기준일: {report_date} (KST)\n"
        f"- 첨부: {report_date}.html (브라우저에서 열람)\n\n"
        "※ 본 메일은 자동 발송됩니다.\n"
    )

    send_email(subject, body, attachments=attachments)


if __name__ == "__main__":
    main()
