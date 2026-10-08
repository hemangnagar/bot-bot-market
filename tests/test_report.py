import json

import report.build as rb
from runner.log import TransactionLog
from runner.meter import SpendMeter
from runner.pricing import Usage


def test_report_builds_from_synthetic_runs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    base = runs / "c-base"
    log = TransactionLog(base / "bench.sqlite")
    meter = SpendMeter(base / "meter.sqlite", cap_usd=80)
    for i in range(6):
        for policy in ("direct", "via_broker"):
            txn = f"c-base-S{i:03d}-{policy[0]}"
            fee = 0.005 if policy == "via_broker" else 0.0
            meter.record(actor="buyer", txn=txn, model="claude-haiku-5-5", usage=Usage(10, 500, 1400, 0, 2000))
            if policy == "via_broker":
                meter.record(actor="broker", txn=txn, model="claude-sonnet-5-5", usage=Usage(2, 400, 1500, 0, 3000))
            log.write({"run_id": "c-base", "txn_id": txn, "scenario_id": f"S{i:03d}", "policy": policy, "task_type": "scrub_basic" if i % 2 else "chain",
                       "providers": "edshield", "price_paid": 0.02 + fee, "broker_fee": fee, "provider_cost": 0.02,
                       "llm_cost_broker": meter.cost_for_txn(txn, "broker"), "llm_cost_buyer": meter.cost_for_txn(txn, "buyer"),
                       "llm_cost_provider": 0.0, "sdk_cost_buyer": 0.03, "sdk_cost_broker": 0.01, "success": int(i != 4), "latency_ms": 9000,
                       "failure_reason": "" if i != 4 else "pii leaked: name", "quote_accepted": 1 if policy == "via_broker" else None,
                       "declined_by_broker": 0, "broker_turns": 3, "buyer_turns": 4, "broker_memory_used": int(i > 1), "result_altered": 0,
                       "fee_pct": 15, "model_broker": "claude-sonnet-5-5", "model_buyer": "claude-haiku-5-5", "ts": 1000 + i},
                      {"notes": "example", "broker_tools": ["search_services", "submit_quote"], "llm_calls": meter.calls_for_txn(txn)})
    log.export_csv(base / "transactions.csv")
    (runs / "c.manifest.json").write_text(json.dumps({"campaign": "c", "n": 6, "runs": [{"name": "base", "run_id": "c-base"}]}))
    out_dir = tmp_path / "report"
    out_dir.mkdir()
    monkeypatch.setattr(rb, "RUNS", runs)
    monkeypatch.setattr(rb, "OUT_DIR", out_dir)
    monkeypatch.setattr(rb, "OUT_MD", out_dir / "economics.md")
    md = rb.build("c", cap=80).read_text()
    assert "Net margin per brokered deal" in md and "## 6. What a real deployment would change" in md
    assert "pii leaked (provider output)" in md
    assert (out_dir / "fee_sensitivity.csv").exists() and (out_dir / "transactions-base.csv").exists()
