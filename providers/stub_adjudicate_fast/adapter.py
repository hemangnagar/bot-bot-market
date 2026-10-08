"""Stub adjudicator: a deterministic heuristic verdict with a thin rationale.

Cheaper and faster than decision_gate, but it never actually reasons: it scores
each claim on hedging words, absolutes and numeric support, then maps the score
gap onto ACT / WAIT / ABANDON. Same output shape as the decision_gate provider.
"""
from __future__ import annotations

import re

ABSOLUTES = re.compile(r"\b(always|never|guaranteed|impossible|certainly|every|all|none)\b", re.I)
HEDGES = re.compile(r"\b(might|may|could|possibly|perhaps|likely|unlikely|some|often)\b", re.I)
NUMBERS = re.compile(r"\d")


def _score(claim: str) -> float:
    return 1.0 - 0.6 * len(ABSOLUTES.findall(claim)) + 0.2 * len(HEDGES.findall(claim)) + 0.3 * min(len(NUMBERS.findall(claim)), 3)


def invoke(payload: dict) -> dict:
    a, b = payload.get("claim_a"), payload.get("claim_b")
    if not isinstance(a, str) or not isinstance(b, str) or not a.strip() or not b.strip():
        raise ValueError("payload.claim_a and payload.claim_b (non-empty strings) are required")
    sa, sb = _score(a), _score(b)
    gap = sa - sb
    if gap >= 0.5:
        verdict, rule = "ACT", "claim_a materially better supported"
    elif gap <= -0.5:
        verdict, rule = "ABANDON", "claim_b materially better supported"
    else:
        verdict, rule = "WAIT", "claims are close; more evidence needed"
    return {
        "verdict": verdict,
        "matched_rule": rule,
        "rationale": f"heuristic scores a={sa:.2f} b={sb:.2f}; absolutes penalised, hedges and figures rewarded",
        "triggering_challenges": [],
        "confidence": "low",
    }
