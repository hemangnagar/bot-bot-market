"""SimLedger: deterministic, zero-cost settlement backed by SQLite.

Wallets are plain strings. A payer signs a payment payload with an HMAC over a
shared secret (the stand-in for an on-chain signature); the ledger verifies the
signature, the nonce (replay protection), the amount, and the balance, then
moves funds and writes one ledger row: from, to, amount, fee_to_broker, tx_id.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .base import PaymentHeader, PaymentRequirement, SettlementResult

SIM_NETWORK = "sim:ledger"
SIM_SCHEME = "exact"
SIM_ASSET = "USD"
ATOMIC = 1_000_000  # 6 decimal places, like USDC


def to_atomic(amount_usd: float) -> str:
    return str(int(round(amount_usd * ATOMIC)))


class SimLedger:
    name = "sim"

    def __init__(self, db_path: str | Path, secret: str | None = None):
        self.db_path = str(db_path)
        self.secret = (secret or os.environ.get("BENCH_SIM_SECRET") or "sim-ledger-secret").encode()
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(
            """
            CREATE TABLE IF NOT EXISTS wallets (wallet TEXT PRIMARY KEY, balance REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ledger (
                tx_id TEXT PRIMARY KEY, ts REAL NOT NULL, "from" TEXT NOT NULL, "to" TEXT NOT NULL,
                amount REAL NOT NULL, fee_to_broker REAL NOT NULL DEFAULT 0, nonce TEXT NOT NULL UNIQUE,
                resource TEXT, memo TEXT
            );
            """
        )

    # -- wallet admin -------------------------------------------------------
    def fund(self, wallet: str, amount_usd: float) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO wallets(wallet, balance) VALUES(?, ?) "
                "ON CONFLICT(wallet) DO UPDATE SET balance = balance + excluded.balance",
                (wallet, float(amount_usd)),
            )

    def balance(self, wallet: str) -> float:
        row = self._con.execute("SELECT balance FROM wallets WHERE wallet = ?", (wallet,)).fetchone()
        return float(row[0]) if row else 0.0

    def ledger_rows(self) -> list[dict]:
        cur = self._con.execute('SELECT tx_id, ts, "from", "to", amount, fee_to_broker, nonce, resource, memo FROM ledger ORDER BY ts')
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # -- Settlement protocol -----------------------------------------------
    def quote(self, payee: str, amount_usd: float, resource: str = "", description: str = "") -> PaymentRequirement:
        return PaymentRequirement(
            scheme=SIM_SCHEME, network=SIM_NETWORK, amount=to_atomic(amount_usd), asset=SIM_ASSET,
            pay_to=payee, resource=resource, description=description, max_timeout_seconds=60,
            extra={"nonce": uuid.uuid4().hex},
        )

    # Payer side: build the PAYMENT-SIGNATURE payload for a requirement.
    def sign(self, payer: str, req: PaymentRequirement, memo: str = "") -> PaymentHeader:
        nonce = req.extra.get("nonce") or uuid.uuid4().hex
        payload = {"from": payer, "to": req.pay_to, "amount": req.amount, "nonce": nonce, "memo": memo}
        payload["signature"] = self._sig(payload)
        return PaymentHeader(x402_version=2, scheme=req.scheme, network=req.network, accepted=req, payload=payload)

    def _sig(self, payload: dict) -> str:
        msg = "|".join(str(payload[k]) for k in ("from", "to", "amount", "nonce"))
        return hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()

    def verify(self, payment: PaymentHeader) -> SettlementResult:
        p, req = payment.payload, payment.accepted
        base = dict(payer=p.get("from", "?"), payee=req.pay_to, amount_usd=req.amount_usd, network=payment.network, tx_id=None)
        if payment.network != SIM_NETWORK or payment.scheme != SIM_SCHEME:
            return SettlementResult(success=False, error="unsupported scheme/network", **base)
        if p.get("to") != req.pay_to or str(p.get("amount")) != str(req.amount):
            return SettlementResult(success=False, error="payload does not match requirement", **base)
        if not hmac.compare_digest(p.get("signature", ""), self._sig(p)):
            return SettlementResult(success=False, error="bad signature", **base)
        if self._con.execute("SELECT 1 FROM ledger WHERE nonce = ?", (p["nonce"],)).fetchone():
            return SettlementResult(success=False, error="nonce already settled (replay)", **base)
        if self.balance(p["from"]) + 1e-9 < req.amount_usd:
            return SettlementResult(success=False, error="insufficient funds", **base)
        return SettlementResult(success=True, **base)

    def verify_and_settle(self, payment: PaymentHeader, fee_to_broker: float = 0.0) -> SettlementResult:
        with self._lock:
            res = self.verify(payment)
            if not res.success:
                return res
            p = payment.payload
            amount = payment.accepted.amount_usd
            tx_id = hashlib.sha256(f"{p['from']}|{p['to']}|{p['amount']}|{p['nonce']}".encode()).hexdigest()[:16]
            self._con.execute("BEGIN")
            try:
                self._con.execute("UPDATE wallets SET balance = balance - ? WHERE wallet = ?", (amount, p["from"]))
                self._con.execute(
                    "INSERT INTO wallets(wallet, balance) VALUES(?, ?) ON CONFLICT(wallet) DO UPDATE SET balance = balance + excluded.balance",
                    (p["to"], amount),
                )
                self._con.execute(
                    'INSERT INTO ledger(tx_id, ts, "from", "to", amount, fee_to_broker, nonce, resource, memo) VALUES(?,?,?,?,?,?,?,?,?)',
                    (tx_id, time.time(), p["from"], p["to"], amount, float(fee_to_broker), p["nonce"], payment.accepted.resource, p.get("memo", "")),
                )
                self._con.execute("COMMIT")
            except Exception:
                self._con.execute("ROLLBACK")
                raise
            res.tx_id = tx_id
            return res

    def close(self) -> None:
        self._con.close()
