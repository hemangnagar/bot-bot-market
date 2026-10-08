"""Provider adapter for github.com/hemangnagar/grocery_optimizer (MIT), run as a sidecar under the
upstream repo's own Python 3.12 venv (.upstream/grocery_optimizer/.venv, see scripts/setup_upstream.sh).

Item list + store set in, cheapest basket out. Each call creates a basket through the public
`baskets` / `basket_items` schema, resolves items with `silver.basket.candidates_for_term`, asks
`silver.verdict.build_verdict` for the single-store verdict, then filters to the requested stores.
Prices come from the credential-free demo dataset seeded by `grocery-seed-demo`.
"""
from __future__ import annotations

import threading
import uuid

from grocery_optimizer.db import get_connection, init_db
from grocery_optimizer.silver.basket import candidates_for_term
from grocery_optimizer.silver.verdict import build_verdict

STORES = {"aldi_kcl": "Aldi", "kroger": "Harris Teeter", "traderjoes": "Trader Joe's", "wholefoods": "Whole Foods"}
_ALIASES = {v.lower(): k for k, v in STORES.items()} | {k: k for k in STORES} | {"harris teeter (kroger)": "kroger", "trader joes": "traderjoes", "whole foods market": "wholefoods"}
_lock = threading.Lock()
_con = None


def _db():
    global _con
    if _con is None:
        _con = get_connection()
        init_db(_con)
    return _con


def _store_key(name: str) -> str | None:
    return _ALIASES.get(str(name).strip().lower())


def invoke(payload: dict) -> dict:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("payload.items (non-empty list of item names or {name, qty}) is required")
    wanted = payload.get("stores") or list(STORES)
    keys = [_store_key(s) for s in wanted]
    if any(k is None for k in keys):
        raise ValueError(f"unknown store in {wanted}; known: {sorted(STORES.values())}")
    norm = []
    for it in items:
        if isinstance(it, str):
            norm.append((it, 1))
        elif isinstance(it, dict) and it.get("name"):
            norm.append((str(it["name"]), int(it.get("qty") or 1)))
        else:
            raise ValueError(f"bad item {it!r}")

    with _lock:
        con = _db()
        basket_id = con.execute(
            "INSERT INTO baskets (name, household_size, is_synthetic, notes) VALUES (?, ?, true, ?) RETURNING basket_id",
            [f"bench:{uuid.uuid4().hex[:8]}", 1, "broker-bench request"],
        ).fetchone()[0]
        matched, unmatched, chosen = [], [], set()
        try:
            for name, qty in norm:
                cands = [c for c in candidates_for_term(con, name) if c["canonical_id"] not in chosen]
                if not cands:
                    unmatched.append(name)
                    continue
                c = cands[0]
                chosen.add(c["canonical_id"])
                con.execute("INSERT INTO basket_items (basket_id, canonical_id, category, label, quantity) VALUES (?, ?, ?, ?, ?)",
                            [basket_id, c["canonical_id"], c.get("coarse_category"), name, qty])
                matched.append({"item": name, "matched_to": c["name"], "qty": qty})
            verdict = build_verdict(con, basket_id) if matched else {"n_items": 0, "flexible": {"stores": []}, "split_footnote": None}
        finally:
            con.execute("DELETE FROM basket_items WHERE basket_id = ?", [basket_id])
            con.execute("DELETE FROM baskets WHERE basket_id = ?", [basket_id])

    stores = []
    for st in verdict["flexible"]["stores"]:
        if st["source"] in keys:
            stores.append({"store": STORES[st["source"]], "store_key": st["source"], "total_usd": round(float(st["store_total"]), 2),
                           "full_coverage": bool(st["full_coverage"]), "missing": list(st.get("missing_labels") or [])})
    covering = [s for s in stores if s["full_coverage"]]
    winner = min(covering, key=lambda s: s["total_usd"]) if covering else None
    return {
        "winner": winner, "stores": sorted(stores, key=lambda s: s["total_usd"]), "matched_items": matched, "unmatched_items": unmatched,
        "n_items": verdict["n_items"], "split_footnote": verdict.get("split_footnote"),
    }
