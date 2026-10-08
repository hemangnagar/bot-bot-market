"""Run loop: pairs every scenario with both buyer policies, logs every transaction, enforces the cap.

    python -m runner.run --n 20 --smoke          # phase-1 smoke run with projection
    python -m runner.run --n 300                 # full run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from agents.broker import Broker
from agents.buyer import Buyer, BuyerOutcome
from agents.env import child_env, read_runtime_key, scrub_process_env
from registry.client import RegistryClient, WalletClient
from settlement.base import BudgetExceeded
from settlement.sim import SimLedger

from .log import TransactionLog
from .meter import MeterServer, SpendMeter
from .scenarios import altered, generate, judge, load_config
from .services import Bench

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
PHASE1_PROVIDERS = ["stub_scrub_cheap", "stub_adjudicate_fast", "edshield"]
ALL_PROVIDERS = PHASE1_PROVIDERS + ["decision_gate", "grocery", "model_evidence"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="broker-bench run loop")
    ap.add_argument("--n", type=int, default=20, help="number of scenarios (each runs under every policy)")
    ap.add_argument("--policies", default="direct,via_broker")
    ap.add_argument("--fee-pct", type=float, default=15.0)
    ap.add_argument("--fee-floor", type=float, default=0.005)
    ap.add_argument("--cap", type=float, default=80.0, help="hard USD cap for this run's API spend")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--providers", default=",".join(ALL_PROVIDERS), help=f"phase 1 set: {','.join(PHASE1_PROVIDERS)}")
    ap.add_argument("--buyer-model", default="claude-haiku-5-5")
    ap.add_argument("--broker-model", default="claude-sonnet-5-5")
    ap.add_argument("--buyer-effort", default="low")
    ap.add_argument("--broker-effort", default="medium")
    ap.add_argument("--no-memory", action="store_true", help="disable the broker's provider-outcome memory")
    ap.add_argument("--smoke", action="store_true", help="print cost per transaction and a projection for --full-n")
    ap.add_argument("--full-n", type=int, default=300)
    ap.add_argument("--abort-if-over-cap", action="store_true", help="exit 2 if the smoke projection exceeds the cap")
    return ap.parse_args(argv)


class Runner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S")
        self.dir = RUNS / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.api_key = read_runtime_key()
        scrub_process_env()
        self.meter = SpendMeter(self.dir / "meter.sqlite", cap_usd=args.cap)
        self.meter_server = MeterServer(self.meter).start()
        self.ledger = SimLedger(self.dir / "ledger.sqlite")
        provider_env = {"ANTHROPIC_API_KEY": self.api_key, "ANTHROPIC_BASE_URL": self.meter_server.url,
                        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        self.bench = Bench(self.ledger, provider_names=[p for p in args.providers.split(",") if p], provider_env=provider_env).start()
        self.registry = RegistryClient(self.bench.registry_url)
        self.log = TransactionLog(self.dir / "bench.sqlite")
        self.ledger.fund("wallet:broker", 100.0)
        self.broker = Broker(registry=self.registry, wallet=WalletClient(self.registry, self.ledger, "wallet:broker"),
                             meter_url=self.meter_server.url, api_key=self.api_key, model=args.broker_model, effort=args.broker_effort,
                             fee_pct=args.fee_pct, fee_floor_usd=args.fee_floor, use_memory=not args.no_memory)
        self.buyer = Buyer(registry=self.registry, ledger=self.ledger, meter_url=self.meter_server.url, api_key=self.api_key,
                           model=args.buyer_model, effort=args.buyer_effort)
        self.stopped_reason: str | None = None
        self.rows: list[dict[str, Any]] = []

    def close(self) -> None:
        self.bench.stop()
        self.meter_server.stop()

    async def one(self, task: dict[str, Any], policy: str) -> dict[str, Any]:
        txn = f"{self.run_id}-{task['id']}-{policy[0]}"
        wallet = WalletClient(self.registry, self.ledger, f"wallet:buyer:{txn}", txn=txn)
        self.ledger.fund(wallet.wallet, task["budget_usd"])
        t0 = time.time()
        if policy == "direct":
            out: BuyerOutcome = await self.buyer.run_direct(task, txn, wallet)
        else:
            out = await self.buyer.run_via_broker(task, txn, wallet, self.broker)
        await asyncio.sleep(0.3)  # let the meter flush the last streamed response
        declined = bool(out.quote and out.quote.declined)
        success, reason = judge(task, result=out.result, success_claimed=out.success_claimed, price_paid_usd=out.price_paid_usd,
                                declined_by_broker=declined, provider_result=out.provider_result or None, finished=out.finished,
                                notes=out.notes)
        result_altered = altered(task, out.result, out.provider_result)
        if not success and out.agent_run and out.agent_run.error:
            reason = f"{reason} [{out.agent_run.error[:120]}]"
        if not success and out.execution_error:
            reason = f"{reason} [{out.execution_error[:120]}]"
        br = out.quote.agent_run if out.quote else None
        row = {
            "run_id": self.run_id, "txn_id": txn, "scenario_id": task["id"], "policy": policy, "task_type": task["task_type"],
            "providers": ",".join(out.providers), "price_paid": round(out.price_paid_usd, 6), "broker_fee": round(out.broker_fee_usd, 6),
            "provider_cost": round(out.price_paid_usd - out.broker_fee_usd, 6) if policy == "via_broker" else round(out.price_paid_usd, 6),
            "llm_cost_broker": self.meter.cost_for_txn(txn, "broker"), "llm_cost_buyer": self.meter.cost_for_txn(txn, "buyer"),
            "llm_cost_provider": self.meter.cost_for_txn(txn, "provider"),
            "sdk_cost_buyer": out.agent_run.sdk_cost_usd if out.agent_run else 0.0, "sdk_cost_broker": br.sdk_cost_usd if br else 0.0,
            "success": int(success), "latency_ms": out.latency_ms, "failure_reason": reason,
            "quote_accepted": None if out.quote_accepted is None else int(out.quote_accepted), "declined_by_broker": int(declined),
            "broker_turns": br.num_turns if br else 0, "buyer_turns": out.agent_run.num_turns if out.agent_run else 0,
            "broker_memory_used": int(bool(out.quote and out.quote.memory_used)), "result_altered": int(result_altered), "fee_pct": self.args.fee_pct,
            "model_broker": self.args.broker_model, "model_buyer": self.args.buyer_model, "ts": t0,
        }
        detail = {"result": out.result, "provider_result": out.provider_result, "notes": out.notes, "stderr": out.agent_run.stderr[-5:] if out.agent_run else [], "calls": out.calls, "buyer_tools": out.agent_run.tool_calls if out.agent_run else [],
                  "broker_tools": br.tool_calls if br else [], "quote": out.quote.for_buyer() if out.quote else None,
                  "plan": out.quote.plan if out.quote else None, "buyer_terminal": out.agent_run.terminal_reason if out.agent_run else None,
                  "broker_terminal": br.terminal_reason if br else None, "llm_calls": self.meter.calls_for_txn(txn)}
        self.log.write(row, detail)
        self.rows.append(row)
        llm = row["llm_cost_broker"] + row["llm_cost_buyer"] + row["llm_cost_provider"]
        print(f"[{len(self.rows):3d}] {task['id']} {policy:10s} {task['task_type']:13s} ok={success!s:5s} paid=${out.price_paid_usd:.3f} "
              f"fee=${out.broker_fee_usd:.3f} llm=${llm:.4f} {out.latency_ms / 1000:.0f}s {reason}", flush=True)
        return row

    async def run(self) -> None:
        cfg = load_config()
        tasks = generate(self.args.n, cfg, seed=self.args.seed)
        (self.dir / "tasks.json").write_text(json.dumps(tasks, indent=1))
        policies = [p for p in self.args.policies.split(",") if p]
        sem = asyncio.Semaphore(self.args.concurrency)

        async def guarded(task: dict[str, Any], policy: str) -> None:
            async with sem:
                if self.stopped_reason:
                    return
                try:
                    await self.one(task, policy)
                except BudgetExceeded as exc:
                    self.stopped_reason = str(exc)
                if self.meter.remaining() <= 0 and not self.stopped_reason:
                    self.stopped_reason = f"cap {self.meter.cap_usd:.2f} USD reached"

        await asyncio.gather(*(guarded(t, p) for t in tasks for p in policies))
        self.finish()

    # -- summary ------------------------------------------------------------
    def finish(self) -> None:
        self.log.export_csv(self.dir / "transactions.csv")
        (self.dir / "broker_memory.json").write_text(json.dumps(self.broker.memory, indent=1))
        (self.dir / "ledger.json").write_text(json.dumps(self.ledger.ledger_rows(), indent=1))
        summary = self.summarize()
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1))
        (self.dir / "summary.md").write_text(render_summary(summary))
        print("\n" + render_summary(summary))
        if self.stopped_reason:
            print(f"\nRUN STOPPED: {self.stopped_reason}")

    def summarize(self) -> dict[str, Any]:
        by_policy: dict[str, Any] = {}
        for policy in sorted({r["policy"] for r in self.rows}):
            rs = [r for r in self.rows if r["policy"] == policy]
            llm = [r["llm_cost_broker"] + r["llm_cost_buyer"] + r["llm_cost_provider"] for r in rs]
            by_policy[policy] = {
                "n": len(rs), "success_rate": sum(r["success"] for r in rs) / len(rs),
                "llm_cost_mean": statistics.mean(llm), "llm_cost_median": statistics.median(llm), "llm_cost_max": max(llm),
                "llm_cost_broker_mean": statistics.mean(r["llm_cost_broker"] for r in rs),
                "llm_cost_buyer_mean": statistics.mean(r["llm_cost_buyer"] for r in rs),
                "fee_mean": statistics.mean(r["broker_fee"] for r in rs), "price_paid_mean": statistics.mean(r["price_paid"] for r in rs),
                "latency_ms_mean": statistics.mean(r["latency_ms"] for r in rs),
                "by_task_type": {t: {"n": len(g), "success_rate": sum(r["success"] for r in g) / len(g),
                                     "llm_cost_mean": statistics.mean(r["llm_cost_broker"] + r["llm_cost_buyer"] + r["llm_cost_provider"] for r in g)}
                                 for t in sorted({r["task_type"] for r in rs}) for g in [[r for r in rs if r["task_type"] == t]]},
            }
        meter = self.meter.summary()
        out: dict[str, Any] = {"run_id": self.run_id, "n_scenarios": self.args.n, "fee_pct": self.args.fee_pct, "by_policy": by_policy,
                               "spend": meter, "stopped_reason": self.stopped_reason, "failures": [
                                   {"txn": r["txn_id"], "policy": r["policy"], "type": r["task_type"], "reason": r["failure_reason"]}
                                   for r in self.rows if not r["success"]]}
        if self.args.smoke and by_policy:
            per_pair = sum(p["llm_cost_mean"] for p in by_policy.values())
            projected = per_pair * self.args.full_n
            out["projection"] = {"full_n": self.args.full_n, "cost_per_scenario_all_policies": per_pair, "projected_usd": projected,
                                 "remaining_cap_usd": self.meter.remaining(), "fits_cap": projected <= self.meter.remaining()}
        return out


def render_summary(s: dict[str, Any]) -> str:
    lines = [f"# Run {s['run_id']}: {s['n_scenarios']} scenarios, fee {s['fee_pct']:.0f}%", ""]
    lines.append("| policy | n | success | mean LLM $/txn | median | max | broker LLM | buyer LLM | mean fee | mean paid | mean latency |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for p, v in s["by_policy"].items():
        lines.append(f"| {p} | {v['n']} | {v['success_rate']:.0%} | {v['llm_cost_mean']:.4f} | {v['llm_cost_median']:.4f} | {v['llm_cost_max']:.4f} | "
                     f"{v['llm_cost_broker_mean']:.4f} | {v['llm_cost_buyer_mean']:.4f} | {v['fee_mean']:.4f} | {v['price_paid_mean']:.4f} | {v['latency_ms_mean'] / 1000:.1f}s |")
    lines += ["", "| policy | task type | n | success | mean LLM $/txn |", "|---|---|---|---|---|"]
    for p, v in s["by_policy"].items():
        for t, g in v["by_task_type"].items():
            lines.append(f"| {p} | {t} | {g['n']} | {g['success_rate']:.0%} | {g['llm_cost_mean']:.4f} |")
    sp = s["spend"]
    lines += ["", f"API spend this run: ${sp['total_usd']:.4f} of cap ${sp['cap_usd']:.2f}", "", "| actor | model | calls | input tok | output tok | cache read | USD |", "|---|---|---|---|---|---|---|"]
    for r in sp["by_actor_model"]:
        lines.append(f"| {r['actor']} | {r['model']} | {r['calls']} | {r['input_tokens']} | {r['output_tokens']} | {r['cache_read']} | {r['cost_usd']:.4f} |")
    if s.get("projection"):
        pj = s["projection"]
        lines += ["", f"Projection for {pj['full_n']} scenarios x all policies: ${pj['projected_usd']:.2f} "
                      f"(${pj['cost_per_scenario_all_policies']:.4f} per scenario) vs remaining cap ${pj['remaining_cap_usd']:.2f}: "
                      f"{'FITS' if pj['fits_cap'] else 'EXCEEDS CAP'}"]
    if s["failures"]:
        lines += ["", "Failures:"] + [f"- {f['txn']} {f['policy']} {f['type']}: {f['reason']}" for f in s["failures"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = Runner(args)
    try:
        asyncio.run(runner.run())
    finally:
        runner.close()
    s = runner.summarize()
    if args.abort_if_over_cap and s.get("projection") and not s["projection"]["fits_cap"]:
        print("ABORT: projected full run exceeds the remaining cap")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
