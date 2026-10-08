"""Provider adapter for github.com/hemangnagar/decision_gate (MIT), pinned by pip to a commit SHA.

Maps "two claims in, verdict + rationale out" onto decision_gate's public `run_review`:
claim_a is the decision under review, claim_b is given as the counter-position in the context.
The Builder and Adversary are Claude Haiku 5.5 calls made through the SpendMeter proxy
(ANTHROPIC_BASE_URL) and attributed to actor `provider:decision_gate`, so this provider's
reasoning cost is metered like everything else. decision_gate's own LiteLLM provider is not
used; the `Provider` protocol only needs `generate_json(system=..., prompt=...)`.
"""
from __future__ import annotations

import os
from typing import Any

from anthropic import Anthropic
from decision_gate.providers import parse_json_object
from decision_gate.runner import run_review

from providers.context import current_txn

MODEL = os.environ.get("BENCH_DECISION_GATE_MODEL", "claude-haiku-5-5")
MAX_ROUNDS_CAP = 2
_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(max_retries=3, timeout=120.0)  # ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL from the sidecar env
    return _client


class MeteredClaudeProvider:
    """decision_gate `Provider`: one Claude call per Builder/Adversary turn, JSON object out."""

    def __init__(self, model: str = MODEL):
        self.model = model

    def generate_json(self, *, system: str, prompt: str) -> dict[str, Any]:
        resp = _get_client().messages.create(
            model=self.model, max_tokens=2500, system=system, messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low"},
            extra_headers={"X-Bench-Actor": "provider:decision_gate", "X-Bench-Txn": current_txn.get()},
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return parse_json_object(text)


def invoke(payload: dict) -> dict:
    a, b = payload.get("claim_a"), payload.get("claim_b")
    if not isinstance(a, str) or not isinstance(b, str) or not a.strip() or not b.strip():
        raise ValueError("payload.claim_a and payload.claim_b (non-empty strings) are required")
    rounds = max(1, min(int(payload.get("max_rounds", 1)), MAX_ROUNDS_CAP))
    context = f"Counter-position to weigh against the decision: {b.strip()}"
    if payload.get("context"):
        context += f"\nAdditional context: {str(payload['context']).strip()}"
    provider = MeteredClaudeProvider()
    ledger = run_review(decision=a.strip(), context=context, builder=provider, adversary=provider, max_rounds=rounds)
    commit = ledger.get("commitment", {})
    challenges = [
        {"id": c.get("id"), "title": c.get("title"), "materiality": c.get("materiality"), "status": c.get("status"),
         "resolves_if": c.get("resolves_if")}
        for c in ledger.get("challenges", [])
    ]
    return {
        "verdict": commit.get("action"),
        "matched_rule": commit.get("matched_rule"),
        "rationale": "; ".join(commit.get("reasons") or []),
        "triggering_challenges": commit.get("triggering_challenges", []),
        "accepted_risks": commit.get("accepted_risks", []),
        "challenges": challenges,
        "n_claims": len(ledger.get("claims", [])),
        "rounds": len(ledger.get("review_rounds", [])),
        "termination": (ledger.get("termination") or {}).get("reason"),
        "confidence": "reviewed",
    }
