"""Sidecar harness: `python -m providers.serve --name <provider> --port <p>`.

Loads providers/<name>/adapter.py, which must expose `invoke(payload: dict) -> dict`,
and serves it as POST /invoke. Each provider runs in its own process so an upstream
repo's dependencies (and interpreter version) never leak into the bench.
"""
from __future__ import annotations

import argparse
import importlib
import json
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent


class InvokeRequest(BaseModel):
    payload: dict


def load_card(name: str) -> dict:
    return json.loads((ROOT / name / "card.json").read_text())


def build_app(name: str) -> FastAPI:
    adapter = importlib.import_module(f"providers.{name}.adapter")
    card = load_card(name)
    app = FastAPI(title=f"provider:{name}")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "provider": name}

    @app.get("/card")
    def get_card() -> dict:
        return card

    @app.post("/invoke")
    def invoke(req: InvokeRequest) -> dict:
        t0 = time.time()
        try:
            out = adapter.invoke(req.payload)
        except ValueError as exc:  # bad input -> 422 so the registry can distinguish it from a crash
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        return {"ok": True, "provider": name, "latency_ms": int((time.time() - t0) * 1000), "output": out}

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    uvicorn.run(build_app(a.name), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
