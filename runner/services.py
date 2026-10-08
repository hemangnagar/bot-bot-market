"""Process lifecycle for the bench: provider sidecars, registry, meter."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from registry.app import build_app, load_cards
from settlement.sim import SimLedger

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not come up")


class ProviderProcess:
    """One sidecar. `python` lets a provider run under its own venv interpreter."""

    def __init__(self, name: str, python: str | None = None, env: dict[str, str] | None = None):
        self.name = name
        self.python = python or sys.executable
        self.port = free_port()
        self.env = env or {}
        self.proc: subprocess.Popen | None = None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ProviderProcess":
        env = {**os.environ, **self.env, "PYTHONPATH": str(ROOT)}
        self.proc = subprocess.Popen([self.python, "-m", "providers.serve", "--name", self.name, "--port", str(self.port)],
                                     cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            wait_http(f"{self.endpoint}/health", timeout=60)
        except RuntimeError:
            err = self.proc.stderr.read().decode() if self.proc.stderr else ""
            raise RuntimeError(f"provider {self.name} failed to start:\n{err[-2000:]}")
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class RegistryServer:
    def __init__(self, ledger: SimLedger, cards: dict[str, dict[str, Any]], host: str = "127.0.0.1"):
        self.port = free_port()
        self.host = host
        self.app = build_app(ledger, cards=cards)
        self._server = uvicorn.Server(uvicorn.Config(self.app, host=host, port=self.port, log_level="warning"))
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "RegistryServer":
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="registry")
        self._thread.start()
        wait_http(f"{self.url}/health")
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)


class Bench:
    """Starts providers + registry against one ledger; registers live endpoints."""

    def __init__(self, ledger: SimLedger, provider_names: list[str] | None = None, provider_pythons: dict[str, str] | None = None):
        self.ledger = ledger
        self.cards = load_cards(provider_names)
        self.providers: dict[str, ProviderProcess] = {}
        self.provider_pythons = provider_pythons or {}
        self.registry: RegistryServer | None = None

    def start(self) -> "Bench":
        for name in self.cards:
            p = ProviderProcess(name, python=self.provider_pythons.get(name)).start()
            self.providers[name] = p
            self.cards[name] = dict(self.cards[name], endpoint=p.endpoint)
        self.registry = RegistryServer(self.ledger, self.cards).start()
        return self

    @property
    def registry_url(self) -> str:
        assert self.registry
        return self.registry.url

    def stop(self) -> None:
        if self.registry:
            self.registry.stop()
        for p in self.providers.values():
            p.stop()
