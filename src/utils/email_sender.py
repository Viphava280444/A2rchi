"""Shared SMTP sender for machine-generated archi mail (playbook scheduler).

Modeled on src/interfaces/redmine_mailer_integration/utils/sender.py and using
the same SENDER_* secrets, but multipart (plain always, HTML from markdown
best-effort) with a single retry. The Redmine integration keeps its own Sender.
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)


class EmailSendError(Exception):
    pass


def _header_safe(value: str) -> str:
    """Collapse CR/LF so a dynamic value can never fold into extra MIME headers."""
    return str(value).replace("\r", " ").replace("\n", " ")


def _render_html(markdown_text: str) -> str:
    import markdown  # optional at runtime; images ship it via requirements-base

    body = markdown.markdown(markdown_text, extensions=["tables", "fenced_code"])
    return (
        "<html><body style=\"font-family: sans-serif; max-width: 720px;\">"
        f"{body}</body></html>"
    )


class EmailSender:
    def __init__(self, from_display_name: Optional[str] = None):
        self.server_name = read_secret("SENDER_SERVER")
        self.port = read_secret("SENDER_PORT")
        self.user = read_secret("SENDER_USER")
        self.password = read_secret("SENDER_PW")
        self.reply_to = read_secret("SENDER_REPLYTO")
        self.from_display_name = from_display_name
        logger.info(
            "EmailSender ready (SERVER:%s PORT:%s USER:%s)",
            self.server_name, self.port, self.user,
        )

    def _build(self, recipients: List[str], subject: str, body_markdown: str,
               banner: Optional[str], footer: Optional[str]) -> MIMEMultipart:
        plain_parts = []
        if banner:
            plain_parts.append(f"*** {banner} ***\n")
        plain_parts.append(body_markdown or "")
        if footer:
            plain_parts.append(f"\n--\n{footer}")
        plain = "\n".join(plain_parts)

        msg = MIMEMultipart("alternative")
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = _header_safe(subject)
        if self.from_display_name:
            from email.utils import formataddr
            msg["From"] = formataddr((_header_safe(self.from_display_name), self.user))
        if self.reply_to:
            msg.add_header("reply-to", self.reply_to)
        msg.attach(MIMEText(plain, "plain"))
        try:
            html = _render_html(
                (f"**{banner}**\n\n" if banner else "") + (body_markdown or "")
                + (f"\n\n---\n*{footer}*" if footer else "")
            )
            msg.attach(MIMEText(html, "html"))
        except Exception as exc:
            logger.warning("HTML rendering unavailable, sending plain only: %s", exc)
        return msg

    def send(self, recipients: List[str], subject: str, body_markdown: str, *,
             banner: Optional[str] = None, footer: Optional[str] = None) -> None:
        if not recipients:
            raise EmailSendError("No recipients")
        for addr in recipients:
            if "\r" in addr or "\n" in addr:
                raise EmailSendError(f"Invalid recipient address: {addr!r}")
        msg = self._build(recipients, subject, body_markdown, banner, footer)
        last_exc = None
        for attempt in (1, 2):
            try:
                with smtplib.SMTP(self.server_name, self.port) as server:
                    server.starttls()
                    server.login(self.user, self.password)
                    server.sendmail(self.user, list(recipients), msg.as_string())
                logger.info("Sent schedule mail to %s (subject: %s)", recipients, subject)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning("SMTP send attempt %d failed: %s", attempt, exc)
        raise EmailSendError(f"SMTP send failed after retry: {last_exc}")
