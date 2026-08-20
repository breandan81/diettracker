"""Outbound email via SMTP (required for email signup verification)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings, get_settings


class SmtpNotConfigured(RuntimeError):
    pass


def smtp_configured(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return bool((s.smtp_host or "").strip() and (s.smtp_from or "").strip() and (s.public_base_url or "").strip())


def require_smtp(settings: Settings | None = None) -> Settings:
    """Registration requires real SMTP — fail closed if missing."""
    s = settings or get_settings()
    missing = []
    if not (s.smtp_host or "").strip():
        missing.append("SMTP_HOST")
    if not (s.smtp_from or "").strip():
        missing.append("SMTP_FROM")
    # Allow unauthenticated SMTP (rare) but require host+from; user/pass typical
    if missing:
        raise SmtpNotConfigured(
            "Email signup requires SMTP. Set "
            + ", ".join(missing)
            + " (and usually SMTP_USER / SMTP_PASSWORD) in secrets.env"
        )
    if not (s.public_base_url or "").strip():
        raise SmtpNotConfigured(
            "Set PUBLIC_BASE_URL (e.g. https://trend.example.com) for verification links"
        )
    return s


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    settings: Settings | None = None,
) -> None:
    s = require_smtp(settings)
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    # Help filters treat this as transactional 1:1 mail
    if s.smtp_user:
        msg["Reply-To"] = s.smtp_user
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    timeout = 30
    if s.smtp_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=timeout, context=context) as smtp:
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=timeout) as smtp:
            smtp.ehlo()
            if s.smtp_starttls:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)


def send_verification_email(*, to: str, verify_url: str, settings: Settings | None = None) -> None:
    # Keep subject/body plain — unicode brand marks + “verify account” often trip spam filters
    subject = "Confirm your trend account"
    text = (
        "Thanks for signing up for trend (weight tracker).\n\n"
        "Confirm this email address by opening the link below.\n"
        "The link expires in 48 hours.\n\n"
        f"{verify_url}\n\n"
        "If you did not create an account, you can ignore this message.\n"
    )
    html = (
        "<p>Thanks for signing up for <strong>trend</strong> (weight tracker).</p>"
        "<p>Confirm this email address (link expires in 48 hours):</p>"
        f'<p><a href="{verify_url}">Confirm email</a></p>'
        f"<p style=\"color:#666;font-size:12px;word-break:break-all\">{verify_url}</p>"
        "<p>If you did not create an account, you can ignore this message.</p>"
    )
    send_email(to=to, subject=subject, text_body=text, html_body=html, settings=settings)
