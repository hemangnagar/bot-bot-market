"""SpendMeter: a local reverse proxy in front of api.anthropic.com that meters every call.

Every runtime process (Claude Code CLI spawned by the Agent SDK, the decision_gate
provider's own calls) is pointed at this proxy via ANTHROPIC_BASE_URL. The proxy
forwards the request unchanged, reads `usage` from the response (JSON or SSE),
prices it with pricing.yaml, stores one row per call, and refuses new requests
with HTTP 402 once the cap is reached. Attribution comes from two request
headers the callers set: X-Bench-Actor and X-Bench-Txn.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from settlement.base import BudgetExceeded

from .pricing import Pricing, Usage

UPSTREAM = "https://api.anthropic.com"
HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
              "proxy-authenticate", "host", "content-length", "accept-encoding"}
ATTRIBUTION = {"x-bench-actor", "x-bench-txn"}


class SpendMeter:
    """Thread-safe cost ledger. `record` prices usage and enforces the cap."""

    def __init__(self, db_path: str | Path, cap_usd: float = 80.0, pricing: Pricing | None = None):
        self.cap_usd = float(cap_usd)
        self.pricing = pricing or Pricing()
        self._lock = threading.RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute(
            """CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT, txn TEXT, model TEXT,
                input_tokens INTEGER, output_tokens INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,
                cache_read INTEGER, cost_usd REAL NOT NULL, status INTEGER, stream INTEGER, latency_ms INTEGER)"""
        )

    def total(self) -> float:
        return float(self._con.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()[0])

    def remaining(self) -> float:
        return self.cap_usd - self.total()

    def check(self) -> None:
        if self.total() >= self.cap_usd:
            raise BudgetExceeded(f"spend {self.total():.4f} USD reached cap {self.cap_usd:.2f} USD")

    def record(self, *, actor: str, txn: str, model: str, usage: Usage, status: int = 200, stream: bool = False,
               latency_ms: int = 0) -> float:
        cost = self.pricing.cost_usd(model, usage) if model else 0.0
        with self._lock:
            self._con.execute(
                "INSERT INTO llm_calls(ts, actor, txn, model, input_tokens, output_tokens, cache_write_5m, cache_write_1h,"
                " cache_read, cost_usd, status, stream, latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), actor, txn, model, usage.input_tokens, usage.output_tokens, usage.cache_write_5m,
                 usage.cache_write_1h, usage.cache_read, cost, status, int(stream), latency_ms),
            )
        return cost

    def summary(self) -> dict[str, Any]:
        rows = self._con.execute(
            "SELECT actor, model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cache_read), SUM(cost_usd)"
            " FROM llm_calls GROUP BY actor, model ORDER BY actor, model"
        ).fetchall()
        return {
            "cap_usd": self.cap_usd,
            "total_usd": self.total(),
            "by_actor_model": [
                {"actor": a, "model": m, "calls": n, "input_tokens": i, "output_tokens": o, "cache_read": c, "cost_usd": cost}
                for a, m, n, i, o, c, cost in rows
            ],
        }

    def cost_for_txn(self, txn: str, actor_prefix: str | None = None) -> float:
        q, args = "SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE txn = ?", [txn]
        if actor_prefix:
            q += " AND actor LIKE ?"
            args.append(actor_prefix + "%")
        return float(self._con.execute(q, args).fetchone()[0])

    def calls_for_txn(self, txn: str) -> list[dict[str, Any]]:
        cur = self._con.execute("SELECT actor, model, input_tokens, output_tokens, cache_read, cost_usd FROM llm_calls WHERE txn = ?", (txn,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# SSE parsing

def parse_sse_usage(events: list[dict[str, Any]]) -> tuple[str, Usage]:
    """Combine message_start and message_delta usage into one Usage.

    message_start.usage carries input/cache tokens (and a provisional output count);
    message_delta.usage carries the final output_tokens and, on current models,
    cumulative input/cache counts that override message_start.
    """
    model, start, delta = "", {}, {}
    for ev in events:
        t = ev.get("type")
        if t == "message_start":
            msg = ev.get("message") or {}
            model = msg.get("model", model)
            start = msg.get("usage") or {}
        elif t == "message_delta":
            delta = ev.get("usage") or delta
    merged = dict(start)
    for k, v in delta.items():
        if v is not None:
            merged[k] = v
    return model, Usage.from_api(merged)


def _sse_events(buffer: bytes) -> list[dict[str, Any]]:
    out = []
    for block in buffer.decode("utf-8", "replace").split("\n\n"):
        data_lines = [ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            out.append(json.loads("\n".join(data_lines)))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# Proxy app

def build_app(meter: SpendMeter, upstream: str = UPSTREAM) -> Starlette:
    client = httpx.AsyncClient(base_url=upstream, timeout=httpx.Timeout(600.0, connect=30.0))

    async def summary(_: Request) -> JSONResponse:
        return JSONResponse(meter.summary())

    async def proxy(request: Request) -> Response:
        actor = request.headers.get("x-bench-actor", "unattributed")
        txn = request.headers.get("x-bench-txn", "")
        body = await request.body()
        try:
            meter.check()
        except BudgetExceeded as exc:
            return JSONResponse({"type": "error", "error": {"type": "budget_exceeded", "message": str(exc)}}, status_code=402)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP | ATTRIBUTION}
        headers["accept-encoding"] = "identity"  # keep bodies uncompressed so usage can be read and re-sent as-is
        wants_stream = False
        if request.method == "POST" and body:
            try:
                wants_stream = bool(json.loads(body).get("stream"))
            except (json.JSONDecodeError, AttributeError):
                wants_stream = False
        t0 = time.time()
        upstream_req = client.build_request(request.method, request.url.path + (f"?{request.url.query}" if request.url.query else ""),
                                            headers=headers, content=body)
        resp = await client.send(upstream_req, stream=True)
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP | {"content-encoding"}}
        is_messages = request.url.path.rstrip("/").endswith("/messages")

        if wants_stream and is_messages and resp.status_code == 200:
            async def gen():
                buf = bytearray()
                try:
                    async for chunk in resp.aiter_raw():
                        buf.extend(chunk)
                        yield chunk
                finally:
                    await resp.aclose()
                    model, usage = parse_sse_usage(_sse_events(bytes(buf)))
                    meter.record(actor=actor, txn=txn, model=model, usage=usage, status=resp.status_code, stream=True,
                                 latency_ms=int((time.time() - t0) * 1000))
            return StreamingResponse(gen(), status_code=resp.status_code, headers=resp_headers, media_type=resp.headers.get("content-type"))

        content = await resp.aread()
        await resp.aclose()
        if is_messages and resp.status_code == 200:
            try:
                data = json.loads(content)
                meter.record(actor=actor, txn=txn, model=data.get("model", ""), usage=Usage.from_api(data.get("usage")),
                             status=resp.status_code, stream=False, latency_ms=int((time.time() - t0) * 1000))
            except (json.JSONDecodeError, KeyError):
                pass
        return Response(content=content, status_code=resp.status_code, headers=resp_headers)

    return Starlette(routes=[
        Route("/_meter/summary", summary, methods=["GET"]),
        Route("/{path:path}", proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
    ])


class MeterServer:
    """Runs the proxy in a background thread. `url` is what callers put in ANTHROPIC_BASE_URL."""

    def __init__(self, meter: SpendMeter, host: str = "127.0.0.1", port: int = 0, upstream: str = UPSTREAM):
        self.meter = meter
        self.host, self.port = host, port
        self._server = uvicorn.Server(uvicorn.Config(build_app(meter, upstream), host=host, port=port, log_level="warning"))
        self._thread: threading.Thread | None = None

    def start(self) -> "MeterServer":
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="meter")
        self._thread.start()
        deadline = time.time() + 15
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("meter server did not start")
            time.sleep(0.05)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
