from __future__ import annotations

import os
import mimetypes
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from src.config import now_kst


def _req(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")
    return v


def send_email(subject: str, body: str, attachments: list[Path]) -> None:
    host = _req("SMTP_HOST")
    port = int(_req("SMTP_PORT"))
    user = _req("SMTP_USER")
    password = _req("SMTP_PASS")
    mail_from = _req("MAIL_FROM")
    mail_to = [x.strip() for x in _req("MAIL_TO").split(",") if x.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(mail_to)
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

    # STARTTLS (587)
    with smtplib.SMTP(host, port) as server:
        server.ehlo()
        server.starttls()
        server.login(user, password)
        server.send_message(msg)


def resolve_report_date_and_attachments(report_dir: Path) -> tuple[str, list[Path]]:
    """
    Return (report_date, attachments) for email.
    Email attachments policy: HTML only.
    """
    today = now_kst().date().isoformat()
    html_today = report_dir / f"{today}.html"

    # Prefer today's HTML report
    if html_today.exists():
        return today, [html_today]

    # Otherwise, find the latest available HTML report date
    available_dates: set[str] = set()
    for path in report_dir.glob("*.html"):
        if len(path.stem) == 10:
            try:
                _ = date.fromisoformat(path.stem)
                available_dates.add(path.stem)
            except ValueError:
                continue

    if not available_dates:
        # Nothing exists yet; return the expected path (send_email will skip if missing)
        return today, [html_today]

    latest = max(available_dates)
    return latest, [report_dir / f"{latest}.html"]


def main() -> None:
    report_dir = Path("reports")
    report_date, attachments = resolve_report_date_and_attachments(report_dir)

    subject = f"[금융권 언론동향] {report_date} (KST)"

    # Keep body short (spam-safe) and user-oriented. Avoid internal implementation details.
    body = (
        "금융권 일일 언론동향 리포트입니다. (대부업권 중심)\n"
        f"- 기준일: {report_date} (KST)\n"
        f"- 첨부: {report_date}.html (브라우저에서 열람)\n\n"
        "※ 본 메일은 자동 발송됩니다.\n"
    )

    send_email(subject, body, attachments=attachments)


if __name__ == "__main__":
    main()
