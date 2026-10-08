"""Stub PII scrubber: regex only, deliberately lower quality than edshield.

Catches emails, phone numbers and SSNs. Misses names, usernames/handles and
street addresses, which is the quality gap the broker should learn about.
"""
from __future__ import annotations

import re

PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "PHONE_NUM": re.compile(r"(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]?\d{3}[ -.]?\d{4}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def invoke(payload: dict) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("payload.text (non-empty string) is required")
    counts: dict[str, int] = {}
    out = text
    for label, pat in PATTERNS.items():
        out, n = pat.subn(f"[{label}]", out)
        if n:
            counts[label] = n
    return {"redacted_text": out, "entity_counts": counts, "n_entities": sum(counts.values()), "policy": payload.get("policy", "none")}
