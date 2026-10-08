# broker-bench

An agent-to-agent commerce prototype. A **broker bot** (Sonnet-class) finds, quotes and routes paid
services for **buyer bots** (Haiku-class). The deliverable is a unit-economics report: does the broker
capture more in fees than it spends on its own reasoning?

Plan and verified facts: [PLAN.md](PLAN.md). **Results: [report/economics.md](report/economics.md)** (300 scenarios x both policies
plus fee and effort sweeps, $18.29 of API spend). Headline: at a 15% fee the broker's margin is about zero (-$0.0004 per accepted
deal); it turns positive at 25%, and direct buyers match the broker's success rate at a lower total cost on every task type.

## Quick start

```bash
make setup          # uv venv (Python 3.12) + deps + pinned upstream providers
make test           # unit tests, no API calls
make smoke          # phase 1: 20 scenarios x {direct, via_broker}; prints cost per transaction + projection
make run            # phase 2: smoke, then the campaign (300 x both policies, fee 5/10/25% sweeps, broker low effort), then the report
make report         # rebuild report/economics.md and report/*.csv from runs/
```

The runtime API key is read from `BENCH_ANTHROPIC_API_KEY`, `CORPUSCLE_ANTHROPIC_KEY` or `ANTHROPIC_API_KEY`
(first one set). Every runtime call goes through the SpendMeter proxy; the hard cap is `--cap` (default $80).

## How a transaction works

```
buyer (Haiku) --request_quote--> broker (Sonnet) --search--> registry  GET /services?q=
                                     |                                  (keyword + schema match only)
                                     +--submit_quote: plan, fee
buyer --accept_quote--> broker executes plan: for each step
                            POST registry /invoke/{provider}            -> 402 + PAYMENT-REQUIRED
                            POST again with PAYMENT-SIGNATURE           -> registry verifies, forwards to
                                                                           provider sidecar POST /invoke,
                                                                           settles on success, PAYMENT-RESPONSE
                        buyer pays broker total_price (ledger row carries fee_to_broker)
buyer --finish--> judged deterministically against what the providers returned
```

`direct` buyers do the search, payment and chaining themselves. Each scenario runs under both policies, so
direct-vs-broker is a controlled comparison.

## Layout

| Path | What |
|---|---|
| `registry/` | FastAPI registry: cards, dumb discovery, 402-gated proxy; `client.py` has the paying client |
| `providers/<name>/` | `adapter.py` (`invoke(payload) -> dict`), `card.json` (price, latency, schemas), `README.md` (upstream + SHA) |
| `providers/serve.py` | sidecar harness: one process per provider, `POST /invoke` |
| `settlement/` | `Settlement` protocol, `SimLedger` (default), x402 wire format notes |
| `agents/` | `buyer.py`, `broker.py` (Agent SDK agents), `tools.py` (in-process MCP tools), `env.py` (credential isolation) |
| `runner/` | `meter.py` (SpendMeter proxy), `scenarios.py` (generator + judge), `run.py` (loop), `log.py` (SQLite + CSV) |
| `report/` | economics report generator (phase 2) |
| `pricing.yaml` | per-token prices with source URL and fetch date |
| `scenarios.yaml` | task mix, claims, impossible tasks |
| `runs/<run_id>/` | `transactions.csv`, `bench.sqlite`, `meter.sqlite`, `ledger.sqlite`, `summary.md`, `tasks.json` |

## Spend metering

`runner/meter.py` is a reverse proxy on 127.0.0.1. Every agent process gets `ANTHROPIC_BASE_URL` pointed at it and
sends `X-Bench-Actor` / `X-Bench-Txn` headers for attribution. The proxy reads `usage` from each JSON or SSE
response, prices it with `pricing.yaml`, stores a row per call, and answers 402 once the cap is hit. The Agent
SDK's own `total_cost_usd` is logged beside it as a cross-check: for Sonnet 5.5 the two agree within a few
percent; for Haiku 5.5 the CLI logs `unrecognized_model` and its estimate runs far above the metered figure,
so the meter is authoritative.

## Providers

Upstream repos are wrapped, never vendored (see each `providers/<name>/README.md` for the SHA and the exact
call). `make check-no-copy` fails if any 6-line window of this repo appears verbatim upstream.

| Provider | Capability | Price | Source |
|---|---|---|---|
| `edshield` | PII scrub (rules layer) | $0.02 | github.com/hemangnagar/edshield, pip from git |
| `stub_scrub_cheap` | PII scrub (regex, misses names/handles) | $0.01 | stub |
| `stub_adjudicate_fast` | adjudication (heuristic) | $0.03 | stub |
| `decision_gate` | adjudication (Builder/Adversary review on Haiku 5.5, metered) | $0.10 | github.com/hemangnagar/decision_gate, pip from git |
| `grocery` | cheapest basket for an item list and store set | $0.01 | github.com/hemangnagar/grocery_optimizer, sidecar in its own venv |
| `model_evidence` | classifier metrics and pass/fail verdict | $0.05 | github.com/hemangnagar/model-evidence, node subprocess |

Prices are inputs to the experiment: edit `card.json`. `scripts/setup_upstream.sh` (run by `make setup`) pins the two sidecar
repos under `.upstream/` and seeds the grocery demo database; nothing from any upstream repo is committed here.

## Scenarios

`scenarios.yaml` mixes eight task types: `scrub_basic`, `scrub_strict` (only edshield passes), `adjudicate`, `adjudicate_reasoned`
(only decision_gate's rated challenges pass), `chain` (scrub then adjudicate), `lookup` (grocery), `eval` (model-evidence) and
`impossible`. Every task has a deterministic judge that scores what the providers returned, not what the agent reported.
