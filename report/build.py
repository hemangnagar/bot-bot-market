"""Economics report generator. Phase 1: renders the latest run's summary into report/economics.md.
Phase 2 adds the full P&L, direct-vs-broker, fee sensitivity, failure modes and deployment sections."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from runner.run import render_summary

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUT = ROOT / "report" / "economics.md"


def latest_run(prefix: str = "") -> Path | None:
    dirs = sorted(p for p in RUNS.glob(f"{prefix}*") if (p / "summary.json").exists())
    return dirs[-1] if dirs else None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run = Path(argv[0]) if argv else latest_run()
    if run is None:
        print("no completed run under runs/")
        return 1
    summary = json.loads((run / "summary.json").read_text())
    OUT.write_text("# broker-bench economics (phase 1 summary)\n\n" + render_summary(summary))
    print(f"wrote {OUT} from {run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
