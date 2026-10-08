"""Per-request context for provider sidecars (which bench transaction is being served)."""
from __future__ import annotations

import contextvars

current_txn: contextvars.ContextVar[str] = contextvars.ContextVar("current_txn", default="")
