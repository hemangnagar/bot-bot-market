"""Price a Claude API `usage` object from pricing.yaml. Nothing here is hardcoded from memory."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICING = ROOT / "pricing.yaml"
_DATE_SUFFIX = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0

    @classmethod
    def from_api(cls, usage: dict[str, Any] | None) -> "Usage":
        """Build from the `usage` object of a Messages API response."""
        u = usage or {}
        creation = u.get("cache_creation") or {}
        w5 = int(creation.get("ephemeral_5m_input_tokens") or 0)
        w1 = int(creation.get("ephemeral_1h_input_tokens") or 0)
        if not creation and u.get("cache_creation_input_tokens"):
            w5 = int(u["cache_creation_input_tokens"])  # older shape: assume 5m writes
        return cls(
            input_tokens=int(u.get("input_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            cache_write_5m=w5,
            cache_write_1h=w1,
            cache_read=int(u.get("cache_read_input_tokens") or 0),
        )

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_write_5m + self.cache_write_1h + self.cache_read


class Pricing:
    def __init__(self, path: Path | str = DEFAULT_PRICING):
        data = yaml.safe_load(Path(path).read_text())
        self.source: str = data["source"]
        self.fetched: str = str(data["fetched"])
        self.models: dict[str, Any] = data["models"]
        self.aliases: dict[str, str] = data.get("aliases", {})

    def resolve(self, model: str) -> str:
        if model in self.models:
            return model
        if model in self.aliases:
            return self.aliases[model]
        stripped = _DATE_SUFFIX.sub("", model)
        if stripped in self.models:
            return stripped
        raise KeyError(f"no pricing for model {model!r}; add it to pricing.yaml")

    def rates(self, model: str, prompt_tokens: int = 0) -> dict[str, float]:
        entry = self.models[self.resolve(model)]
        if "tiers" in entry:
            for tier in entry["tiers"]:
                cap = tier.get("max_prompt_tokens")
                if cap is None or prompt_tokens <= cap:
                    return {k: float(v) for k, v in tier.items() if k != "max_prompt_tokens"}
            raise ValueError("pricing tiers must end with max_prompt_tokens: null")
        return {k: float(v) for k, v in entry.items()}

    def cost_usd(self, model: str, usage: Usage) -> float:
        r = self.rates(model, usage.prompt_tokens)
        per = 1_000_000
        return (
            usage.input_tokens * r["input"]
            + usage.output_tokens * r["output"]
            + usage.cache_write_5m * r["cache_write_5m"]
            + usage.cache_write_1h * r["cache_write_1h"]
            + usage.cache_read * r["cache_read"]
        ) / per
