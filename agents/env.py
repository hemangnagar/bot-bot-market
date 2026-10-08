"""Credential and environment isolation for runtime agents.

The Claude Code session that builds this repo has its own credentials and its own
ANTHROPIC_BASE_URL. Runtime agents must bill the owner's API key only, through the
SpendMeter. So: read the key once, scrub every CLAUDE_*/ANTHROPIC_* variable from
this process, and hand each child an explicit environment.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / ".bench" / "claude-config"
KEY_VARS = ("BENCH_ANTHROPIC_API_KEY", "CORPUSCLE_ANTHROPIC_KEY", "ANTHROPIC_API_KEY")


def read_runtime_key() -> str:
    for var in KEY_VARS:
        val = os.environ.get(var)
        if val:
            return val
    raise RuntimeError(f"no API key found; set one of {KEY_VARS}")


def scrub_process_env() -> None:
    """Drop inherited Claude/Anthropic variables so nothing leaks into children."""
    for k in list(os.environ):
        if k.startswith(("CLAUDE", "ANTHROPIC_")) or k in KEY_VARS:
            os.environ.pop(k, None)


def child_env(*, actor: str, txn: str, meter_url: str, api_key: str) -> dict[str, str]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_BASE_URL": meter_url,
        "ANTHROPIC_AUTH_TOKEN": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "ANTHROPIC_CUSTOM_HEADERS": f"X-Bench-Actor: {actor}\nX-Bench-Txn: {txn}",
        "CLAUDE_CONFIG_DIR": str(CONFIG_DIR),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
