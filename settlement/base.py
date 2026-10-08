"""Settlement protocol shared by SimLedger (default) and X402Testnet (phase 3).

The wire shapes mirror x402 protocol v2 (python package `x402` 2.25.0): a 402
response carries a `PAYMENT-REQUIRED` header, the retried request carries a
`PAYMENT-SIGNATURE` header, and the paid response carries `PAYMENT-RESPONSE`.
Each header is base64(JSON). Deviations from the real protocol are listed in
settlement/README.md.
"""
from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

X402_VERSION = 2
HDR_REQUIRED = "PAYMENT-REQUIRED"
HDR_SIGNATURE = "PAYMENT-SIGNATURE"
HDR_RESPONSE = "PAYMENT-RESPONSE"


def b64(obj: Any) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()).decode()


def unb64(s: str) -> Any:
    return json.loads(base64.b64decode(s.encode()).decode())


@dataclass
class PaymentRequirement:
    """One entry of the 402 `accepts` list."""

    scheme: str
    network: str
    amount: str  # atomic units as a string, as in x402 (USD cents*10^4 for sim; USDC 6dp on chain)
    asset: str
    pay_to: str
    resource: str
    description: str = ""
    max_timeout_seconds: int = 60
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def amount_usd(self) -> float:
        return int(self.amount) / 1_000_000

    def to_wire(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "network": self.network,
            "amount": self.amount,
            "asset": self.asset,
            "payTo": self.pay_to,
            "resource": self.resource,
            "description": self.description,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "extra": self.extra,
        }

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> "PaymentRequirement":
        return cls(
            scheme=d["scheme"], network=d["network"], amount=str(d["amount"]), asset=d["asset"],
            pay_to=d["payTo"], resource=d.get("resource", ""), description=d.get("description", ""),
            max_timeout_seconds=int(d.get("maxTimeoutSeconds", 60)), extra=dict(d.get("extra") or {}),
        )


def payment_required_body(accepts: list[PaymentRequirement], error: str = "Payment required") -> dict[str, Any]:
    return {"x402Version": X402_VERSION, "error": error, "accepts": [a.to_wire() for a in accepts]}


@dataclass
class PaymentHeader:
    """Decoded `PAYMENT-SIGNATURE` payload. `payload` is scheme-specific."""

    x402_version: int
    scheme: str
    network: str
    accepted: PaymentRequirement
    payload: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "x402Version": self.x402_version,
            "scheme": self.scheme,
            "network": self.network,
            "accepted": self.accepted.to_wire(),
            "payload": self.payload,
        }

    @classmethod
    def from_wire(cls, d: dict[str, Any]) -> "PaymentHeader":
        return cls(
            x402_version=int(d.get("x402Version", X402_VERSION)),
            scheme=d["scheme"], network=d["network"],
            accepted=PaymentRequirement.from_wire(d["accepted"]), payload=dict(d["payload"]),
        )


@dataclass
class SettlementResult:
    success: bool
    tx_id: str | None
    payer: str
    payee: str
    amount_usd: float
    network: str
    error: str | None = None

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        d["transaction"] = d.pop("tx_id")
        return d


class BudgetExceeded(RuntimeError):
    """Raised by the SpendMeter when the configured USD cap is reached."""


class InsufficientFunds(RuntimeError):
    pass


class Settlement(Protocol):
    name: str

    def quote(self, payee: str, amount_usd: float, resource: str = "", description: str = "") -> PaymentRequirement: ...

    def verify(self, payment: PaymentHeader) -> SettlementResult: ...

    def verify_and_settle(self, payment: PaymentHeader, fee_to_broker: float = 0.0) -> SettlementResult: ...

    def balance(self, wallet: str) -> float: ...
