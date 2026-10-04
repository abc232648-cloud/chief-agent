from __future__ import annotations

from typing import Any

STRONG_SCAM_SIGNALS = (
    "pay a fee", "registration fee", "training fee", "send money", "buy equipment",
    "gift card", "crypto payment", "bank transfer to", "otp", "password", "deposit",
)
SUSPICIOUS_SIGNALS = (
    "telegram only", "whatsapp only", "guaranteed income", "no interview", "act immediately",
)


def screen_job(job: dict[str, Any]) -> dict[str, Any]:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    strong = [s for s in STRONG_SCAM_SIGNALS if s in text]
    suspicious = [s for s in SUSPICIOUS_SIGNALS if s in text]
    if strong:
        status = "CONFIRMED_SCAM"
    elif suspicious:
        status = "SUSPICIOUS"
    else:
        status = "NO_OBVIOUS_SCAM"
    return {"status": status, "signals": sorted(set(strong + suspicious))}
