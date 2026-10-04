from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class EmailSettings:
    """Provider-neutral SMTP delivery settings.

    SMTP is the transport protocol; the actual mailbox/provider is chosen by the user.
    Credentials are intentionally environment-only and are never stored in SQLite.
    """
    provider: str
    host: str
    port: int
    username: str
    sender: str
    recipient: str
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.sender and self.recipient)


def load_email_settings(env: dict[str, str] | None = None) -> EmailSettings:
    from control.settings import environment
    e = environment(env)
    provider = e.get("EMAIL_PROVIDER", "custom_smtp")
    presets = {
        "custom_smtp": (e.get("SMTP_HOST", ""), int(e.get("SMTP_PORT", "587")), True),
        "gmail": (e.get("SMTP_HOST", "smtp.gmail.com"), int(e.get("SMTP_PORT", "587")), True),
        "outlook": (e.get("SMTP_HOST", "smtp.office365.com"), int(e.get("SMTP_PORT", "587")), True),
        "yahoo": (e.get("SMTP_HOST", "smtp.mail.yahoo.com"), int(e.get("SMTP_PORT", "587")), True),
    }
    if provider not in presets:
        raise ValueError(f"Unsupported EMAIL_PROVIDER: {provider}")
    host, port, tls = presets[provider]
    return EmailSettings(
        provider=provider,
        host=host,
        port=port,
        username=e.get("SMTP_USERNAME", ""),
        sender=e.get("EMAIL_SENDER", e.get("SMTP_USERNAME", "")),
        recipient=e.get("EMAIL_RECIPIENT", ""),
        use_tls=tls,
    )


def public_email_config(env: dict[str, str] | None = None) -> dict[str, object]:
    s = load_email_settings(env)
    return {
        "provider": s.provider,
        "host": s.host,
        "port": s.port,
        "username": s.username,
        "sender": s.sender,
        "recipient": s.recipient,
        "configured": s.configured,
        "password_configured": any(__import__('control.settings',fromlist=['environment']).environment(env).get(k,'') for k in ('SMTP_PASSWORD','SMTP_PASSWORD_REF')),
        "password_is_never_returned": True,
    }
