import pytest

from notifications.email_config import load_email_settings, public_email_config


def test_custom_smtp_is_provider_neutral():
    s = load_email_settings({
        "EMAIL_PROVIDER": "custom_smtp", "SMTP_HOST": "mail.example.test",
        "SMTP_PORT": "2525", "SMTP_USERNAME": "me@example.test",
        "EMAIL_SENDER": "me@example.test", "EMAIL_RECIPIENT": "audit@example.test",
    })
    assert s.host == "mail.example.test"
    assert s.port == 2525
    assert s.recipient == "audit@example.test"
    assert s.configured


def test_provider_presets_do_not_store_password():
    env = {"EMAIL_PROVIDER": "gmail", "SMTP_USERNAME": "me@example.com", "EMAIL_RECIPIENT": "audit@example.com", "SMTP_PASSWORD": "secret"}
    s = public_email_config(env)
    assert s["host"] == "smtp.gmail.com"
    assert s["password_configured"] is True
    assert "password" not in s


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        load_email_settings({"EMAIL_PROVIDER": "random_mail"})
