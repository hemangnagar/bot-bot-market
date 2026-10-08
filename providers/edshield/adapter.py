"""Provider adapter for github.com/hemangnagar/edshield (Apache-2.0).

Calls the installed package's public `deidentify()`; nothing from the repo is
copied here. Runs rules-only (`model_name="rules"`), so no torch and no network.
"""
from __future__ import annotations

from edshield import deidentify

POLICIES = {"ferpa", "coppa", "research"}


def invoke(payload: dict) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("payload.text (non-empty string) is required")
    policy = payload.get("policy") or "ferpa"
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {sorted(POLICIES)}")
    res = deidentify(text, policy=policy, model_name="rules", verify=True)
    counts = dict(res.audit.get("by_label") or {})
    return {
        "redacted_text": res.deidentified_text,
        "entity_counts": counts,
        "n_entities": len(res.entities),
        "policy": res.policy,
        "method": res.method,
    }
