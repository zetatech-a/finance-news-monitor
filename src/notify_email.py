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
    today = now_kst().date().isoformat()
    md = report_dir / f"{today}.md"
    html = report_dir / f"{today}.html"

    if md.exists() or html.exists():
        return today, [md, html]

    available_dates: set[str] = set()
    for path in report_dir.glob("*.md"):
        if len(path.stem) == 10:
            try:
                _ = date.fromisoformat(path.stem)
                available_dates.add(path.stem)
            except ValueError:
                continue
    for path in report_dir.glob("*.html"):
        if len(path.stem) == 10:
            try:
                _ = date.fromisoformat(path.stem)
                available_dates.add(path.stem)
            except ValueError:
                continue

    if not available_dates:
        return today, [md, html]

    latest = max(available_dates)
    return latest, [report_dir / f"{latest}.md", report_dir / f"{latest}.html"]


def main() -> None:
    report_dir = Path("reports")
    report_date, attachments = resolve_report_date_and_attachments(report_dir)

    subject = f"[금융권 언론동향] {report_date}"
    body = (
        f"금융권(대부업권 중심) 일일 언론동향 리포트입니다.\n\n"
        f"- 날짜: {report_date}\n"
        f"- 첨부: {attachments[0].name}, {attachments[1].name}\n"
        f"- 저장: GitHub repo의 reports/ 폴더에 누적\n"
    )

    send_email(subject, body, attachments=attachments)


if __name__ == "__main__":
    main()
