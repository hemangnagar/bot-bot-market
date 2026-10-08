"""Client-side helpers: discovery and the paying 402 handshake.

`WalletClient` is what buyer and broker tools use: it retries a 402 with a signed
PAYMENT-SIGNATURE header built by the Settlement implementation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from settlement.base import HDR_SIGNATURE, PaymentRequirement, b64, unb64
from settlement.sim import SimLedger


class ProviderError(RuntimeError):
    def __init__(self, provider: str, status: int, detail: str):
        super().__init__(f"{provider} failed ({status}): {detail}")
        self.provider, self.status, self.detail = provider, status, detail


@dataclass
class InvokeResult:
    provider: str
    output: dict[str, Any]
    price_paid_usd: float
    tx_id: str | None
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)


class RegistryClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._c = httpx.Client(base_url=self.base_url, timeout=180.0)

    def search(self, q: str = "", input_key: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": q}
        if input_key:
            params["input_key"] = input_key
        return self._c.get("/services", params=params).json()

    def card(self, name: str) -> dict[str, Any]:
        r = self._c.get(f"/services/{name}")
        r.raise_for_status()
        return r.json()

    def register(self, card: dict[str, Any]) -> None:
        self._c.post("/providers/register", json=card).raise_for_status()

    def quote_402(self, name: str, payload: dict[str, Any]) -> tuple[PaymentRequirement, dict[str, Any]]:
        r = self._c.post(f"/invoke/{name}", json=payload)
        if r.status_code == 404:
            raise ProviderError(name, 404, "unknown provider")
        if r.status_code != 402:
            raise ProviderError(name, r.status_code, f"expected 402, got {r.text[:200]}")
        body = r.json()
        return PaymentRequirement.from_wire(body["accepts"][0]), body

    def invoke_paid(self, name: str, payload: dict[str, Any], signature_wire: dict[str, Any], txn: str = "") -> httpx.Response:
        return self._c.post(f"/invoke/{name}", json=payload, headers={HDR_SIGNATURE: b64(signature_wire), "X-Bench-Txn": txn})


class WalletClient:
    """A payer: a wallet id plus the ability to sign SimLedger payments."""

    def __init__(self, registry: RegistryClient, ledger: SimLedger, wallet: str, txn: str = ""):
        self.registry, self.ledger, self.wallet = registry, ledger, wallet
        self.txn = txn  # default transaction id for attribution; the broker passes one per call
        self.spent_usd = 0.0
        self.calls: list[InvokeResult] = []

    def balance(self) -> float:
        return self.ledger.balance(self.wallet)

    def invoke(self, name: str, payload: dict[str, Any], memo: str = "", txn: str | None = None) -> InvokeResult:
        t0 = time.time()
        req, _ = self.registry.quote_402(name, payload)
        if self.balance() + 1e-9 < req.amount_usd:
            raise ProviderError(name, 402, f"wallet {self.wallet} has {self.balance():.4f} USD, price is {req.amount_usd:.4f} USD")
        header = self.ledger.sign(self.wallet, req, memo=memo)
        r = self.registry.invoke_paid(name, payload, header.to_wire(), txn=txn if txn is not None else self.txn)
        if r.status_code != 200:
            try:
                detail = r.json().get("error") or r.json().get("detail") or r.text
            except ValueError:
                detail = r.text
            raise ProviderError(name, r.status_code, str(detail)[:300])
        data = r.json()
        res = InvokeResult(provider=name, output=data.get("output", {}), price_paid_usd=float(data.get("price_paid_usd", req.amount_usd)),
                           tx_id=(data.get("settlement") or {}).get("transaction"), latency_ms=int((time.time() - t0) * 1000), raw=data)
        self.spent_usd += res.price_paid_usd
        self.calls.append(res)
        return res
