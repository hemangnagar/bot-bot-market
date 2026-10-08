"""Registry: capability cards, dumb discovery, and a 402-gated proxy to providers.

Discovery is keyword + schema-key match only; the broker does the smart matching.
Every provider call goes through POST /invoke/{name}:
  1. no PAYMENT-SIGNATURE header -> 402 with PAYMENT-REQUIRED (header + JSON body)
  2. with PAYMENT-SIGNATURE -> Settlement.verify -> forward to provider -> on provider
     success Settlement.verify_and_settle -> response carries PAYMENT-RESPONSE.
Settling after the provider answers is the x402 default flow (verify, execute, settle).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from settlement.base import (HDR_REQUIRED, HDR_RESPONSE, HDR_SIGNATURE, PaymentHeader, Settlement, b64,
                             payment_required_body, unb64)

PROVIDERS_DIR = Path(__file__).resolve().parents[1] / "providers"
_WORD = re.compile(r"[a-z0-9]+")


def load_cards(names: list[str] | None = None) -> dict[str, dict[str, Any]]:
    cards = {}
    for card_path in sorted(PROVIDERS_DIR.glob("*/card.json")):
        card = json.loads(card_path.read_text())
        if names is None or card["name"] in names:
            cards[card["name"]] = card
    return cards


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def match_score(card: dict[str, Any], q: str, input_key: str | None) -> float:
    """Dumb keyword match: fraction of query tokens found in name/description/tags, plus a schema bonus."""
    if not q and not input_key:
        return 1.0
    hay = _tokens(card["name"]) | _tokens(card["description"]) | set(t.lower() for t in card.get("tags", []))
    qt = _tokens(q)
    score = (len(qt & hay) / len(qt)) if qt else 0.0
    if input_key and input_key in (card.get("input_schema", {}).get("properties") or {}):
        score += 0.5
    return score


def build_app(settlement: Settlement, cards: dict[str, dict[str, Any]] | None = None, fee_lookup=None) -> FastAPI:
    app = FastAPI(title="broker-bench registry")
    app.state.cards = cards if cards is not None else load_cards()
    app.state.settlement = settlement
    app.state.calls: list[dict[str, Any]] = []
    client = httpx.Client(timeout=120.0)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "providers": sorted(app.state.cards)}

    @app.post("/providers/register")
    def register(card: dict[str, Any]) -> dict:
        for k in ("name", "description", "input_schema", "output_schema", "price_usd", "latency_p50_ms", "endpoint", "wallet"):
            if k not in card:
                raise HTTPException(422, f"card missing {k}")
        app.state.cards[card["name"]] = card
        return {"ok": True, "name": card["name"]}

    @app.get("/services")
    def services(q: str = "", input_key: str | None = None, min_score: float = 0.01) -> list[dict[str, Any]]:
        scored = [(match_score(c, q, input_key), c) for c in app.state.cards.values()]
        scored = [(s, c) for s, c in scored if s >= min_score]
        scored.sort(key=lambda sc: (-sc[0], sc[1]["price_usd"]))
        return [dict(c, match_score=round(s, 3)) for s, c in scored]

    @app.get("/services/{name}")
    def service(name: str) -> dict[str, Any]:
        if name not in app.state.cards:
            raise HTTPException(404, f"unknown provider {name}")
        return app.state.cards[name]

    @app.post("/invoke/{name}")
    async def invoke(name: str, request: Request, payment_signature: str | None = Header(default=None, alias=HDR_SIGNATURE)):
        card = app.state.cards.get(name)
        if card is None:
            raise HTTPException(404, f"unknown provider {name}")
        if not card.get("endpoint"):
            raise HTTPException(503, f"provider {name} has no live endpoint")
        resource = f"/invoke/{name}"
        if payment_signature is None:
            req = settlement.quote(card["wallet"], float(card["price_usd"]), resource=resource, description=card["description"][:80])
            body = payment_required_body([req])
            return JSONResponse(body, status_code=402, headers={HDR_REQUIRED: b64(body)})
        try:
            payment = PaymentHeader.from_wire(unb64(payment_signature))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"malformed {HDR_SIGNATURE}: {exc}") from exc
        if payment.accepted.pay_to != card["wallet"] or payment.accepted.amount_usd + 1e-9 < float(card["price_usd"]):
            raise HTTPException(402, "payment does not match this provider's requirement")
        pre = settlement.verify(payment)
        if not pre.success:
            return JSONResponse({"error": pre.error, "x402Version": 2}, status_code=402)

        payload = await request.json()
        t0 = time.time()
        try:
            resp = client.post(card["endpoint"].rstrip("/") + "/invoke", json={"payload": payload},
                               headers={"X-Bench-Txn": request.headers.get("x-bench-txn", "")})
        except httpx.HTTPError as exc:
            app.state.calls.append({"provider": name, "ok": False, "error": str(exc), "ts": t0})
            raise HTTPException(502, f"provider {name} unreachable: {exc}") from exc
        latency_ms = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            app.state.calls.append({"provider": name, "ok": False, "error": resp.text[:200], "ts": t0, "latency_ms": latency_ms})
            # No settlement on provider failure: the buyer keeps the funds.
            return JSONResponse({"ok": False, "provider": name, "error": resp.text[:500], "settled": False, "latency_ms": latency_ms},
                                status_code=resp.status_code)
        settled = settlement.verify_and_settle(payment)
        if not settled.success:  # raced out of funds between verify and settle
            return JSONResponse({"error": settled.error, "x402Version": 2}, status_code=402)
        app.state.calls.append({"provider": name, "ok": True, "ts": t0, "latency_ms": latency_ms, "tx_id": settled.tx_id})
        out = resp.json()
        out["settlement"] = settled.to_wire()
        out["price_paid_usd"] = payment.accepted.amount_usd
        return JSONResponse(out, headers={HDR_RESPONSE: b64(settled.to_wire())})

    return app
