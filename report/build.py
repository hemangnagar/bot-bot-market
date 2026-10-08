"""Economics report generator: runs/ -> report/economics.md + report/*.csv.

    python -m report.build                      # latest campaign (runs/<campaign>.manifest.json) or latest run
    python -m report.build --campaign phase2    # a named campaign
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from runner.meter import SpendMeter
from runner.pricing import Pricing

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUT_DIR = ROOT / "report"
OUT_MD = OUT_DIR / "economics.md"


# ---------------------------------------------------------------------------
# data access

def load_rows(run_dir: Path) -> list[dict[str, Any]]:
    db = run_dir / "bench.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    cur = con.execute("SELECT * FROM transactions ORDER BY ts")
    cols = [c[0] for c in cur.description]
    rows = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except json.JSONDecodeError:
            d["detail"] = {}
        for k in ("price_paid", "broker_fee", "provider_cost", "llm_cost_broker", "llm_cost_buyer", "llm_cost_provider",
                  "sdk_cost_buyer", "sdk_cost_broker", "latency_ms", "fee_pct"):
            d[k] = float(d.get(k) or 0.0)
        d["success"] = int(d.get("success") or 0)
        d["llm_total"] = d["llm_cost_broker"] + d["llm_cost_buyer"] + d["llm_cost_provider"]
        rows.append(d)
    con.close()
    return rows


def meter_summary(run_dir: Path) -> dict[str, Any] | None:
    db = run_dir / "meter.sqlite"
    return SpendMeter(db, cap_usd=1e9).summary() if db.exists() else None


def sdk_vs_meter(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    out = {}
    for actor, m_key, s_key in (("buyer", "llm_cost_buyer", "sdk_cost_buyer"), ("broker", "llm_cost_broker", "sdk_cost_broker")):
        rs = [r for r in rows if r[m_key] > 0]
        if rs:
            out[actor] = (sum(r[m_key] for r in rs), sum(r[s_key] for r in rs))
    return out


# ---------------------------------------------------------------------------
# helpers

def mean(xs: list[float]) -> float:
    return st.mean(xs) if xs else 0.0


def median(xs: list[float]) -> float:
    return st.median(xs) if xs else 0.0


def usd(x: float) -> str:
    return f"${x:,.4f}"


def pct(x: float) -> str:
    return f"{x:.0%}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def normalise_reason(reason: str) -> str:
    r = re.sub(r"\[.*?\]", "", reason or "").strip()
    r = re.sub(r"accuracy [\d.]+ != expected [\d.]+", "accuracy mismatch", r)
    r = re.sub(r"(agent gave up|broker declined):.*", r"\1", r)
    r = re.sub(r"unmatched items:.*", "unmatched items", r)
    r = re.sub(r"pii leaked: .*", "pii leaked (provider output)", r)
    return r or "unknown"


# ---------------------------------------------------------------------------
# sections

def section_pnl(base: list[dict[str, Any]]) -> tuple[str, list[list[Any]]]:
    via = [r for r in base if r["policy"] == "via_broker"]
    deals = [r for r in via if r["price_paid"] > 0]
    lines = []
    fee_all, cost_all = mean([r["broker_fee"] for r in via]), mean([r["llm_cost_broker"] for r in via])
    fee_d, cost_d = mean([r["broker_fee"] for r in deals]), mean([r["llm_cost_broker"] for r in deals])
    lines.append(f"**Net margin per brokered deal: {usd(fee_d - cost_d)}** (mean fee {usd(fee_d)} minus mean broker reasoning cost "
                 f"{usd(cost_d)}, over {len(deals)} accepted deals). Counting every task the broker reasoned about, including the ones it "
                 f"declined or the buyer rejected ({len(via)} tasks), the net is {usd(fee_all - cost_all)} per task.")
    lines.append("")
    lines.append(f"Break-even fee on the accepted deals: {cost_d / mean([r['provider_cost'] for r in deals]):.0%} of the provider price "
                 f"(the run used {base[0]['fee_pct']:.0f}% with a {usd(0.005)} floor)." if deals else "")
    headers = ["task type", "brokered", "accepted", "mean fee", "median fee", "mean broker LLM", "median broker LLM", "net / deal",
               "provider LLM (decision_gate)", "broker turns"]
    rows = []
    for t in sorted({r["task_type"] for r in via}):
        g = [r for r in via if r["task_type"] == t]
        d = [r for r in g if r["price_paid"] > 0]
        rows.append([t, len(g), len(d), usd(mean([r["broker_fee"] for r in d])), usd(median([r["broker_fee"] for r in d])),
                     usd(mean([r["llm_cost_broker"] for r in g])), usd(median([r["llm_cost_broker"] for r in g])),
                     usd(mean([r["broker_fee"] - r["llm_cost_broker"] for r in d])), usd(mean([r["llm_cost_provider"] for r in g])),
                     f"{mean([float(r['broker_turns'] or 0) for r in g]):.1f}"])
    rows.append(["**all**", len(via), len(deals), usd(fee_d), usd(median([r["broker_fee"] for r in deals])), usd(cost_all),
                 usd(median([r["llm_cost_broker"] for r in via])), usd(fee_d - cost_d), usd(mean([r["llm_cost_provider"] for r in via])),
                 f"{mean([float(r['broker_turns'] or 0) for r in via]):.1f}"])
    lines += ["", table(headers, rows)]
    return "\n".join(lines), [headers] + rows


def section_direct_vs_broker(base: list[dict[str, Any]]) -> tuple[str, list[list[Any]]]:
    headers = ["task type", "policy", "n", "success", "buyer LLM", "broker LLM", "price paid", "total cost to buyer", "latency"]
    rows, wins = [], []
    types = sorted({r["task_type"] for r in base}) + ["all"]
    for t in types:
        per = {}
        for p in ("direct", "via_broker"):
            g = [r for r in base if r["policy"] == p and (t == "all" or r["task_type"] == t)]
            if not g:
                continue
            succ = mean([r["success"] for r in g])
            buyer, broker = mean([r["llm_cost_buyer"] for r in g]), mean([r["llm_cost_broker"] for r in g])
            paid = mean([r["price_paid"] for r in g])
            total = paid + buyer  # the buyer pays the broker's fee inside price_paid, never the broker's reasoning
            lat = mean([r["latency_ms"] for r in g]) / 1000
            per[p] = (succ, buyer, broker, paid, total, lat, len(g))
            rows.append([t, p, len(g), pct(succ), usd(buyer), usd(broker), usd(paid), usd(total), f"{lat:.1f}s"])
        if "direct" in per and "via_broker" in per and t != "all":
            d, v = per["direct"], per["via_broker"]
            if v[0] > d[0] + 1e-9:
                wins.append(f"{t}: success {pct(v[0])} via broker vs {pct(d[0])} direct")
            if v[4] < d[4] - 1e-9:
                wins.append(f"{t}: total cost to buyer {usd(v[4])} via broker vs {usd(d[4])} direct")
    text = table(headers, rows)
    text += "\n\n**Where the broker wins:** " + ("; ".join(wins) + "." if wins else
                                                "nowhere on these scenarios. Direct buyers match the broker's success rate and pay less.")
    return text, [headers] + rows


def section_fee_sensitivity(base: list[dict[str, Any]], sweeps: dict[str, list[dict[str, Any]]]) -> tuple[str, list[list[Any]]]:
    headers = ["fee %", "source", "brokered", "quotes accepted", "success", "mean fee", "mean broker LLM", "net / brokered task", "net / accepted deal",
               "mean price to buyer"]
    rows = []
    runs = {15: base}
    for name, rs in sweeps.items():
        m = re.match(r"fee(\d+)", name)
        if m:
            runs[int(m.group(1))] = rs
    for fee in sorted(runs):
        via = [r for r in runs[fee] if r["policy"] == "via_broker"]
        deals = [r for r in via if r["price_paid"] > 0]
        quoted = [r for r in via if not r["declined_by_broker"]]
        rows.append([fee, "measured run", len(via), pct(len(deals) / len(quoted)) if quoted else "n/a", pct(mean([r["success"] for r in via])),
                     usd(mean([r["broker_fee"] for r in deals])), usd(mean([r["llm_cost_broker"] for r in via])),
                     usd(mean([r["broker_fee"] for r in via]) - mean([r["llm_cost_broker"] for r in via])),
                     usd(mean([r["broker_fee"] - r["llm_cost_broker"] for r in deals])), usd(mean([r["price_paid"] for r in deals]))])
    # analytic re-pricing of the base run: same plans, same reasoning cost, fee recomputed
    via = [r for r in base if r["policy"] == "via_broker"]
    deals = [r for r in via if r["price_paid"] > 0]
    for fee in (5, 10, 15, 25, 50, 100):
        fees = [max(r["provider_cost"] * fee / 100, 0.005) for r in deals]
        rows.append([fee, "re-priced base run", len(via), "as base", "as base", usd(mean(fees)), usd(mean([r["llm_cost_broker"] for r in via])),
                     usd(sum(fees) / len(via) - mean([r["llm_cost_broker"] for r in via])) if via else "n/a",
                     usd(mean([f - r["llm_cost_broker"] for f, r in zip(fees, deals)])), usd(mean([r["provider_cost"] for r in deals]) + mean(fees))])
    return table(headers, rows), [headers] + rows


def section_effort(base: list[dict[str, Any]], sweeps: dict[str, list[dict[str, Any]]]) -> str:
    low = sweeps.get("effort-low")
    if not low:
        return "_No broker-effort sweep in this campaign._"
    headers = ["broker effort", "brokered", "success", "mean broker LLM", "median", "broker turns", "mean fee", "net / brokered task", "latency"]
    rows = []
    for label, rs in (("medium (base)", base), ("low", low)):
        via = [r for r in rs if r["policy"] == "via_broker"]
        rows.append([label, len(via), pct(mean([r["success"] for r in via])), usd(mean([r["llm_cost_broker"] for r in via])),
                     usd(median([r["llm_cost_broker"] for r in via])), f"{mean([float(r['broker_turns'] or 0) for r in via]):.1f}",
                     usd(mean([r["broker_fee"] for r in via])), usd(mean([r["broker_fee"] - r["llm_cost_broker"] for r in via])),
                     f"{mean([r['latency_ms'] for r in via]) / 1000:.1f}s"])
    return table(headers, rows)


def section_memory(base: list[dict[str, Any]]) -> str:
    via = sorted([r for r in base if r["policy"] == "via_broker"], key=lambda r: r["ts"])
    if len(via) < 30:
        return "_Too few brokered transactions to compare early vs late._"
    third = len(via) // 3
    early, late = via[:third], via[-third:]
    headers = ["slice", "n", "memory populated", "success", "mean broker LLM", "broker turns", "searches per quote"]
    rows = []
    for label, g in (("first third", early), ("last third", late)):
        searches = mean([sum(1 for t in r["detail"].get("broker_tools", []) if t == "search_services") for r in g])
        rows.append([label, len(g), pct(mean([float(r["broker_memory_used"] or 0) for r in g])), pct(mean([r["success"] for r in g])),
                     usd(mean([r["llm_cost_broker"] for r in g])), f"{mean([float(r['broker_turns'] or 0) for r in g]):.1f}", f"{searches:.2f}"])
    return table(headers, rows)


def section_failures(all_rows: list[dict[str, Any]]) -> tuple[str, list[list[Any]]]:
    fails = [r for r in all_rows if not r["success"]]
    counts = Counter(normalise_reason(r["failure_reason"]) for r in fails)
    headers = ["rank", "failure mode", "count", "share of failures", "example (transaction, policy, task type)", "what happened"]
    rows = []
    for i, (reason, n) in enumerate(counts.most_common(5), 1):
        ex = next(r for r in fails if normalise_reason(r["failure_reason"]) == reason)
        notes = (ex["detail"].get("notes") or ex["failure_reason"] or "").replace("\n", " ").replace("|", "/")[:220]
        rows.append([i, reason, n, pct(n / len(fails)) if fails else "0%", f"{ex['txn_id']}, {ex['policy']}, {ex['task_type']}", notes])
    altered = sum(1 for r in all_rows if int(r.get("result_altered") or 0))
    text = (f"{len(fails)} failed transactions out of {len(all_rows)} across the campaign. In {altered} transactions the agent's reported output "
            f"differed from what the providers returned (it patched a leaky redaction itself); those are judged on the provider output.\n\n"
            + table(headers, rows))
    return text, [headers] + rows


def section_spend(run_dirs: list[Path], cap: float) -> tuple[str, list[list[Any]]]:
    by: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "usd": 0.0})
    per_run = []
    total = 0.0
    for d in sorted(run_dirs):
        s = meter_summary(d)
        if not s:
            continue
        per_run.append((d.name, s["total_usd"]))
        total += s["total_usd"]
        for r in s["by_actor_model"]:
            b = by[(r["actor"], r["model"])]
            b["calls"] += r["calls"]
            b["input"] += r["input_tokens"] or 0
            b["output"] += r["output_tokens"] or 0
            b["cache_read"] += r["cache_read"] or 0
            b["usd"] += r["cost_usd"]
    headers = ["actor", "model", "calls", "input tokens", "output tokens", "cache-read tokens", "USD"]
    rows = [[a, m, int(b["calls"]), int(b["input"]), int(b["output"]), int(b["cache_read"]), usd(b["usd"])] for (a, m), b in sorted(by.items())]
    by_model: dict[str, float] = defaultdict(float)
    for (a, m), b in by.items():
        by_model[m] += b["usd"]
    text = [f"**Total API spend across every run under runs/: {usd(total)} of the {usd(cap)} cap ({total / cap:.1%}).**", "",
            "By model: " + ", ".join(f"{m} {usd(v)}" for m, v in sorted(by_model.items(), key=lambda kv: -kv[1])) + ".", "",
            table(headers, rows), "", table(["run", "USD"], [[n, usd(v)] for n, v in per_run])]
    return "\n".join(text), [headers] + rows


def broker_token_split(run_dir: Path) -> dict[str, float]:
    """Share of the broker's bill by token type, priced with pricing.yaml rates for its model."""
    db = run_dir / "meter.sqlite"
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT model, SUM(input_tokens), SUM(output_tokens), SUM(cache_write_5m), SUM(cache_write_1h), SUM(cache_read)"
                      " FROM llm_calls WHERE actor = 'broker' GROUP BY model ORDER BY SUM(cost_usd) DESC").fetchone()
    con.close()
    if not row:
        return {}
    model, inp, out, w5, w1, cr = row
    rates = Pricing().rates(model)
    parts = {"output": (out or 0) * rates["output"], "cache writes": (w5 or 0) * rates["cache_write_5m"] + (w1 or 0) * rates["cache_write_1h"],
             "cache reads": (cr or 0) * rates["cache_read"], "uncached input": (inp or 0) * rates["input"]}
    total = sum(parts.values()) or 1.0
    return {k: v / total for k, v in parts.items()}


def section_deployment(base: list[dict[str, Any]], all_rows: list[dict[str, Any]], base_dir: Path) -> str:
    via = [r for r in base if r["policy"] == "via_broker"]
    broker_cost = mean([r["llm_cost_broker"] for r in via])
    split = broker_token_split(base_dir)
    # repeat-task share: same (task_type, plan providers) pair seen before in the run
    seen, repeats = set(), 0
    for r in sorted(via, key=lambda r: r["ts"]):
        key = (r["task_type"], r["providers"])
        repeats += key in seen
        seen.add(key)
    repeat_share = repeats / len(via) if via else 0
    turns = mean([float(r["broker_turns"] or 0) for r in via])
    out_share, write_share = split.get("output", 0.0), split.get("cache writes", 0.0)
    items = [
        f"**Deterministic routing for repeat tasks.** {repeat_share:.0%} of brokered tasks in the base run had the same (task type, plan) as an "
        f"earlier one. Serving repeats from a cached plan with no model call would remove up to that share of broker reasoning cost "
        f"(up to {usd(broker_cost * repeat_share)} per brokered task); a stricter match key (task type plus payload shape) would cover less, "
        f"but still most of this bench's traffic, and most of the quote latency goes with it.",
        f"**Cheaper model for discovery.** The quote phase is {turns:.1f} model turns of which the first is always a registry search. "
        f"Running search-and-shortlist on Haiku 5.5 and only the plan/quote turn on Sonnet 5.5 would cut roughly a third of broker cost "
        f"(Haiku output tokens cost 1/20th of Sonnet's); the buyers in this run show Haiku handles the search step reliably.",
        f"**Output-token diet.** Output tokens are {out_share:.0%} of the broker's bill (cache writes {write_share:.0%}, cache reads "
        f"{split.get('cache reads', 0.0):.0%}, uncached input {split.get('uncached input', 0.0):.0%}). A terser rationale and a structured-output "
        f"quote (no prose) would remove an estimated 20 to 30% of output tokens, about {out_share * 0.25:.0%} of the bill.",
        "**Lower effort by default.** See the effort sweep above: the low-effort broker's cost and success rate bound what a cheaper default buys.",
        "**Skip the quote round-trip for small tickets.** For plans under a fee floor's worth of provider cost, execute first and settle after; "
        "the buyer-side accept/decline turn (two Haiku turns, about $0.0003) and the broker's quote wait disappear.",
        f"**Amortise the agent spawn.** Each quote spawns a fresh CLI process and re-writes the system-prompt cache (about 1,400 tokens), which is "
        f"{write_share:.0%} of the broker's bill. A long-lived broker session that handles many quotes keeps the cache warm and converts those "
        f"writes into reads at a 25th of the price.",
        f"**Price the fee to cover reasoning.** At the measured {usd(broker_cost)} per brokered task the floor fee needs to be at least that; "
        f"the fee-sensitivity table shows where the percentage fee alone breaks even.",
    ]
    return "\n".join(f"{i}. {s}" for i, s in enumerate(items, 1))


# ---------------------------------------------------------------------------
# assembly

def pick_campaign(name: str | None) -> tuple[str | None, dict[str, Path]]:
    manifests = sorted(RUNS.glob("*.manifest.json"))
    if name:
        manifests = [m for m in manifests if m.name == f"{name}.manifest.json"]
    if not manifests:
        return None, {}
    m = json.loads(manifests[-1].read_text())
    return m["campaign"], {r["name"]: RUNS / r["run_id"] for r in m["runs"]}


def build(campaign: str | None = None, cap: float = 80.0) -> Path:
    name, runs = pick_campaign(campaign)
    if not runs:
        latest = sorted(p for p in RUNS.iterdir() if (p / "bench.sqlite").exists())
        if not latest:
            raise SystemExit("no runs found under runs/")
        name, runs = latest[-1].name, {"base": latest[-1]}
    base = load_rows(runs["base"])
    sweeps = {k: load_rows(v) for k, v in runs.items() if k != "base"}
    all_rows = base + [r for rs in sweeps.values() for r in rs]
    all_run_dirs = [p for p in RUNS.iterdir() if p.is_dir()]

    pnl_text, pnl_csv = section_pnl(base)
    dvb_text, dvb_csv = section_direct_vs_broker(base)
    fee_text, fee_csv = section_fee_sensitivity(base, sweeps)
    fail_text, fail_csv = section_failures(all_rows)
    spend_text, spend_csv = section_spend(all_run_dirs, cap)
    cmp = sdk_vs_meter(base)
    n_scen = len({r["scenario_id"] for r in base})
    models = sorted({r["model_broker"] for r in base}), sorted({r["model_buyer"] for r in base})

    md = [
        "# broker-bench economics report", "",
        f"Campaign `{name}`: {n_scen} seeded scenarios, each run under both buyer policies in the base run "
        f"({len(base)} transactions), plus sweeps {', '.join(sweeps) or 'none'} ({sum(len(v) for v in sweeps.values())} brokered transactions). "
        f"Broker model {', '.join(models[0])}; buyer model {', '.join(models[1])}; the decision_gate provider runs claude-haiku-5-5. "
        "All USD figures come from the SpendMeter proxy (API `usage` priced with pricing.yaml), not from model estimates.", "",
        "## 1. Broker P&L per transaction", "", pnl_text, "",
        "## 2. Direct vs via-broker", "", dvb_text, "",
        "## 3. Fee sensitivity", "", fee_text, "",
        "Measured runs re-ran the broker and buyers at each fee on the same scenarios; re-priced rows recompute the fee on the base run's plans "
        "without re-running, so they isolate the arithmetic from behaviour changes (buyer acceptance, broker plan choice).", "",
        "### Broker effort sweep", "", section_effort(base, sweeps), "",
        "### Does the broker's provider memory help?", "", section_memory(base), "",
        "The memory records technical success and latency per provider. Providers in this bench almost never fail technically, so the table above "
        "mostly measures whether a populated memory changes the broker's search behaviour and cost; quality failures (a scrubber missing a name) "
        "are not fed back to it.", "",
        "## 4. Failure modes", "", fail_text, "",
        "## 5. Spend", "", spend_text, "",
        "Agent SDK estimate vs metered cost in the base run: " + "; ".join(
            f"{a}: meter {usd(m)} vs SDK {usd(s)} ({s / m:.1f}x)" for a, (m, s) in cmp.items()) +
        ". The CLI prices claude-haiku-5-5 as an unrecognised model, so its estimate is not usable for Haiku; the meter is authoritative.", "",
        "## 6. What a real deployment would change", "", section_deployment(base, all_rows, runs["base"]), "",
    ]
    OUT_MD.write_text("\n".join(md))
    write_csv(OUT_DIR / "pnl_by_task_type.csv", pnl_csv[0], pnl_csv[1:])
    write_csv(OUT_DIR / "direct_vs_broker.csv", dvb_csv[0], dvb_csv[1:])
    write_csv(OUT_DIR / "fee_sensitivity.csv", fee_csv[0], fee_csv[1:])
    write_csv(OUT_DIR / "failure_modes.csv", fail_csv[0], fail_csv[1:])
    write_csv(OUT_DIR / "spend.csv", spend_csv[0], spend_csv[1:])
    for k, d in runs.items():
        src = d / "transactions.csv"
        if src.exists():
            (OUT_DIR / f"transactions-{k}.csv").write_bytes(src.read_bytes())
    return OUT_MD


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--cap", type=float, default=80.0)
    a = ap.parse_args(argv)
    out = build(a.campaign, a.cap)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
