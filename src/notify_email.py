from __future__ import annotations

import os
import mimetypes
import smtplib
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


def main() -> None:
    today = now_kst().date().isoformat()
    report_dir = Path("reports")

    md = report_dir / f"{today}.md"
    html = report_dir / f"{today}.html"

    subject = f"[금융권 언론동향] {today}"
    body = (
        f"금융권(대부업권 중심) 일일 언론동향 리포트입니다.\n\n"
        f"- 날짜: {today}\n"
        f"- 첨부: {md.name}, {html.name}\n"
        f"- 저장: GitHub repo의 reports/ 폴더에 누적\n"
    )

    send_email(subject, body, attachments=[md, html])


if __name__ == "__main__":
    main()
