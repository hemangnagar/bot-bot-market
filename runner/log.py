"""Transaction log: SQLite plus CSV export."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

COLUMNS = [
    "run_id", "txn_id", "scenario_id", "policy", "task_type", "providers", "price_paid", "broker_fee", "provider_cost",
    "llm_cost_broker", "llm_cost_buyer", "llm_cost_provider", "sdk_cost_buyer", "sdk_cost_broker", "success", "latency_ms",
    "failure_reason", "quote_accepted", "declined_by_broker", "broker_turns", "buyer_turns", "broker_memory_used", "result_altered",
    "fee_pct", "model_broker", "model_buyer", "ts",
]


class TransactionLog:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.db_path), check_same_thread=False, isolation_level=None)
        cols = ", ".join(f"{c} TEXT" if c in ("run_id", "txn_id", "scenario_id", "policy", "task_type", "providers", "failure_reason", "model_broker", "model_buyer")
                         else f"{c} REAL" for c in COLUMNS)
        self._con.execute(f"CREATE TABLE IF NOT EXISTS transactions ({cols}, detail TEXT)")

    def write(self, row: dict[str, Any], detail: dict[str, Any] | None = None) -> None:
        vals = [row.get(c) for c in COLUMNS]
        self._con.execute(f"INSERT INTO transactions ({', '.join(COLUMNS)}, detail) VALUES ({', '.join('?' * (len(COLUMNS) + 1))})",
                          vals + [json.dumps(detail or {}, default=str)])

    def rows(self) -> list[dict[str, Any]]:
        cur = self._con.execute(f"SELECT {', '.join(COLUMNS)} FROM transactions ORDER BY ts")
        return [dict(zip(COLUMNS, r)) for r in cur.fetchall()]

    def export_csv(self, path: str | Path) -> None:
        rows = self.rows()
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
