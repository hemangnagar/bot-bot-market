"""Phase-2 campaign: the base run plus the sweeps, all on the same seeded scenario set.

  base      : N scenarios x {direct, via_broker}, fee 15%, broker effort medium
  fee-5/10/25: N scenarios, via_broker only (fee does not touch direct buyers)
  effort-low: N scenarios, via_broker only, fee 15%, broker effort low

Each run has its own runs/<campaign>-<name>/ directory and its own meter DB; the cap is shared
by passing the remaining budget to each successive run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .run import RUNS, Runner, parse_args as parse_run_args
from .meter import SpendMeter


def spent_in(run_dir: Path) -> float:
    db = run_dir / "meter.sqlite"
    return SpendMeter(db, cap_usd=1e9).total() if db.exists() else 0.0


def total_spent_all_runs() -> float:
    return sum(spent_in(p) for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="broker-bench phase-2 campaign")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--cap", type=float, default=80.0, help="overall USD cap across every run under runs/")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--campaign", default=time.strftime("c%Y%m%d-%H%M"))
    ap.add_argument("--fees", default="5,10,25")
    ap.add_argument("--skip-base", action="store_true")
    ap.add_argument("--skip-sweeps", action="store_true")
    a = ap.parse_args(argv)

    plan: list[tuple[str, list[str]]] = []
    if not a.skip_base:
        plan.append(("base", ["--policies", "direct,via_broker", "--fee-pct", "15", "--broker-effort", "medium"]))
    if not a.skip_sweeps:
        for fee in [f for f in a.fees.split(",") if f]:
            plan.append((f"fee{fee}", ["--policies", "via_broker", "--fee-pct", fee, "--broker-effort", "medium"]))
        plan.append(("effort-low", ["--policies", "via_broker", "--fee-pct", "15", "--broker-effort", "low"]))

    manifest_path = RUNS / f"{a.campaign}.manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"campaign": a.campaign, "n": a.n, "runs": []}
    done = {r["name"] for r in manifest["runs"] if not r.get("stopped_reason")}
    plan = [(name, extra) for name, extra in plan if name not in done]
    for name, extra in plan:
        remaining = a.cap - total_spent_all_runs()
        run_id = f"{a.campaign}-{name}"
        if remaining <= 0.5:
            print(f"STOP before {run_id}: only ${remaining:.2f} of the ${a.cap:.2f} cap remains", flush=True)
            break
        print(f"\n=== {run_id}: {' '.join(extra)} (remaining cap ${remaining:.2f}) ===", flush=True)
        args = parse_run_args(["--n", str(a.n), "--run-id", run_id, "--cap", f"{remaining:.4f}", "--concurrency", str(a.concurrency), *extra])
        runner = Runner(args)
        try:
            import asyncio
            asyncio.run(runner.run())
        finally:
            runner.close()
        manifest["runs"].append({"name": name, "run_id": run_id, "args": extra, "spent_usd": spent_in(RUNS / run_id),
                                 "stopped_reason": runner.stopped_reason})
        manifest_path.write_text(json.dumps(manifest, indent=1))
        if runner.stopped_reason:
            print(f"STOP: {runner.stopped_reason}", flush=True)
            break
    print(f"\ncampaign {a.campaign} done; total API spend across runs/: ${total_spent_all_runs():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
