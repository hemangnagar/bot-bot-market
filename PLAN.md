# broker-bench build plan

Status: awaiting owner approval (non-negotiable 4). No code has been written.
Prepared 2026-10-08 against the handoff brief. Everything under "Verified" was
checked today against live docs or the cloned upstream repos, not recalled.

## 1. Verified facts

### Agent SDK
| Item | Value |
|---|---|
| PyPI package | `claude-agent-sdk` 0.2.164 (bundles the Claude Code CLI; no separate install) |
| Import | `from claude_agent_sdk import query, ClaudeAgentOptions, tool, create_sdk_mcp_server, ResultMessage` |
| Custom tools | `@tool(name, description, schema)` + `create_sdk_mcp_server(...)`, exposed as `mcp__<server>__<tool>` |
| Cost/usage | `ResultMessage.total_cost_usd`, `.usage`, `.model_usage[model].costUSD` (client-side estimates) |
| Budget guard | `ClaudeAgentOptions(max_budget_usd=...)` stops a query at a cost estimate |
| Gateway | Claude Code honors `ANTHROPIC_BASE_URL` and `ANTHROPIC_CUSTOM_HEADERS` (used by the SpendMeter proxy, see 3.1) |

### Models and pricing (platform.claude.com/docs/en/about-claude/pricing, fetched 2026-10-08)
| Model | ID | Input | Output | Cache write 5m / 1h | Cache read |
|---|---|---|---|---|---|
| Haiku 5.5 (prompt <= 100K) | `claude-haiku-5-5` | $0.10 | $0.50 | $0.125 / $0.20 | $0.01 |
| Haiku 5.5 (prompt > 100K) | `claude-haiku-5-5` | $0.50 | $2.50 | $0.625 / $1.00 | $0.05 |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2.00 | $10.00 | $2.50 / $4.00 | $0.10 |
| Sonnet 5 | `claude-sonnet-5` | $2.00 | $10.00 | $2.50 / $4.00 | $0.20 |
| Opus 5.5 | `claude-opus-5-5` | $4.00 | $20.00 | $5.00 / $8.00 | $0.20 |
| Haiku 4.5 | `claude-haiku-4-5` | $1.00 | $5.00 | $1.25 / $2.00 | $0.10 |

All USD per million tokens. These go into `pricing.yaml` with the source URL and fetch date.
Proposed roles: buyer = `claude-haiku-5-5`, broker = `claude-sonnet-5-5`.

### x402
| Item | Value |
|---|---|
| PyPI package | `x402` 2.25.0 (MIT, Python >= 3.10), extras `x402[fastapi,httpx,evm]` |
| Protocol | v2 is current. Headers: `PAYMENT-REQUIRED`, `PAYMENT-SIGNATURE`, `PAYMENT-RESPONSE` (v1 `X-PAYMENT` is legacy) |
| Server side | `x402.server.x402ResourceServer`, `x402.http.HTTPFacilitatorClient`, FastAPI `x402.http.middleware.fastapi.PaymentMiddlewareASGI`, `x402.mechanisms.evm.exact.ExactEvmServerScheme` |
| Client side | `x402.x402Client.create_payment_payload(...)` |
| Base Sepolia | network `eip155:84532`, test USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e`, public facilitator `https://x402.org/facilitator` (registers 84532 in the SDK examples) |

Deviation to document: the brief names the payment header `PAYMENT`; the SDK's v2 name is `PAYMENT-SIGNATURE`. I will follow the SDK.

### Upstream repos (cloned read-only, SHAs pinned)
| Repo | SHA | License | Entry point I will use | LLM inside? |
|---|---|---|---|---|
| decision_gate | `d22f183267e0c74d813e3de534393e867ec1393c` | MIT | `decision_gate.runner.run_review(decision, context, builder, adversary, max_rounds)` with my own `Provider` objects (the public `generate_json(system, prompt)` protocol). No litellm needed. Python >= 3.11, zero required deps | Yes: Builder + Adversary calls per review |
| edshield | `a568e73a6c34d40335802235db81950e84004ab1` | Apache-2.0 | `edshield.deidentify(text, policy=..., model_name="rules")` -> `DeidResult.deidentified_text`, `.audit["by_label"]`, `n_entities`. Deps: pyyaml, faker. Rules-only, no torch | No |
| grocery_optimizer | `ef8fa67ae9c6e3f21c7977800b674f3a7c69edb0` | MIT | `grocery-seed-demo` once (4 stores x 26 items, no credentials), then per call: insert a basket through the public `baskets`/`basket_items` schema using `silver.basket.candidates_for_term`, call `silver.verdict.build_verdict(con, basket_id)`, filter to requested stores. Python >= 3.12; heavy deps (duckdb, pandas, playwright wheel, anthropic) so it gets its own venv. DB path is relative to the package root, so it runs from an editable clone under `.upstream/` | No |
| model-evidence | `a0f9025fe54b391ad42f5c908812ee848668a4ae` | **No LICENSE file**, `package.json` has `private: true` and no license field | `node dist/run-evidence.mjs bundle.json policy.json --out verdict.json` (Node 22 present, no npm install). v1 input shape `{name, split, runs:[{id, seed, rows:[{sample_id, y_true, y_pred}]}]}` | No |

Nothing is vendored. Three repos install from their git URL at the SHA; grocery_optimizer and model-evidence are shallow-cloned at the SHA into a gitignored `.upstream/` directory at setup time.

### Toolchain in this environment
Python 3.11/3.12/3.13, `uv` 0.11, Node 22.22, Claude Code CLI 2.1.294. **`ANTHROPIC_API_KEY` is not set.** The session's own `ANTHROPIC_BASE_URL` points at the session proxy and must not be inherited by runtime agents (see 3.1).

## 2. Architecture (as in the brief, with the decisions filled in)

```
broker-bench/
  registry/app.py          FastAPI: cards, GET /services?q=, POST /invoke/{provider} (402 handshake, proxies to sidecar)
  providers/_common.py     sidecar harness: POST /invoke -> adapter.invoke(payload)
  providers/<name>/        adapter.py, card.json, README.md (how upstream is invoked, SHA)
    decision_gate/ edshield/ grocery/ model_evidence/
    stub_scrub_cheap/      regex-only scrub, $0.01, misses usernames/handles (lower quality)
    stub_adjudicate_fast/  heuristic verdict, $0.03, thin rationale (lower quality)
  settlement/base.py       Settlement Protocol + PaymentRequirement/PaymentHeader/SettlementResult
  settlement/sim.py        SimLedger: SQLite wallets + ledger rows (from, to, amount, fee_to_broker, tx_id), HMAC-signed payment headers
  settlement/x402_testnet.py  phase 3 only
  agents/buyer.py          Agent SDK, haiku-5-5, policies direct | via_broker
  agents/broker.py         Agent SDK, sonnet-5-5, discover -> score -> chain -> quote -> execute -> fee
  agents/tools.py          in-process MCP tools shared by both: search_services, get_quote, pay_and_invoke, request_broker_quote, accept_quote, decline
  runner/meter.py          SpendMeter: local metering reverse proxy + pricing + BudgetExceeded
  runner/scenarios.py      generates tasks from scenarios.yaml (seeded)
  runner/run.py            run loop, transaction log (SQLite + CSV)
  report/build.py          -> report/economics.md + report/*.csv
  pricing.yaml  scenarios.yaml  Makefile  pyproject.toml  .upstream/ (gitignored)
```

## 3. Design decisions that need your sign-off

### 3.1 SpendMeter as a local metering proxy (recommended)
The buyer and broker run inside the Claude Code CLI (spawned by the Agent SDK), and the decision_gate provider makes its own Claude calls. The only way to "wrap every Claude API call and read `usage` from each response" across all three is a local reverse proxy on 127.0.0.1 that forwards to `https://api.anthropic.com`, parses `usage` from JSON and SSE responses (`message_start` + `message_delta`), prices it from `pricing.yaml`, writes a row per call (actor, transaction id, model, tokens, USD) to SQLite, and returns HTTP 402 to callers once the cap is reached, which the runner turns into `BudgetExceeded`. Every runtime process gets `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` plus `ANTHROPIC_CUSTOM_HEADERS` carrying `X-Bench-Actor` and `X-Bench-Txn` for attribution. The Agent SDK's `max_budget_usd` is a second, per-query guard, and `ResultMessage.total_cost_usd` is logged next to the meter's figure as a cross-check.

Credential isolation: the runner builds the subprocess environment explicitly (owner's `ANTHROPIC_API_KEY`, the meter URL, nothing from this session's `CLAUDE_*` or `ANTHROPIC_BASE_URL`), so runtime calls bill the $100 credit and never the session.

### 3.2 Agent construction
`query(prompt, ClaudeAgentOptions(model=..., system_prompt=<custom string>, mcp_servers={"bench": server}, allowed_tools=[mcp tools only], tools=[], max_turns=N, max_budget_usd=X, env=<isolated env>))`. One `query()` per agent per transaction. The broker's memory of provider outcomes lives in a SQLite table it reads through a tool, so it persists across queries. Buyer effort low, broker effort medium (both configurable; a lever for section 6 of the report).

### 3.3 decision_gate is the one LLM-backed provider
Each adjudication runs Builder + Adversary on `claude-haiku-5-5` with `max_rounds=1` by default (about 2 to 3 calls, roughly $0.002 to $0.005). Its spend is metered under actor `provider:decision_gate` and logged as an extra column `llm_cost_provider`. The brief's input shape (two claims) maps to `decision=claim_a`, `context="Counter-position: " + claim_b`; the output is the gate action (`ACT`/`WAIT`/`ABANDON`), `matched_rule`, `reasons`, `triggering_challenges`, plus the full ledger.

### 3.4 Fee sensitivity without 4x the spend
The broker's reasoning cost does not depend on `fee_pct`; only the buyer's accept/decline might. Plan: the main 300 brokered transactions run at 15%; margin at 5/10/15/25% is recomputed from the logged provider prices and LLM costs; buyer acceptance at each level is measured with three extra 30-transaction runs (5%, 10%, 25%). Alternative: four full runs, about 4x the broker spend.

### 3.5 Scenario vocabulary
Grocery tasks draw items from the demo catalogue (26 items, stores Harris Teeter / Whole Foods / Aldi / Trader Joe's). Scrub tasks use synthetic student-style text with planted PII. Adjudication tasks are short claim pairs. Eval tasks are synthetic prediction/label arrays. Chain tasks: scrub then adjudicate. Ambiguous tasks: scrub (edshield vs stub_scrub_cheap) and adjudicate (decision_gate vs stub_adjudicate_fast). Impossible tasks: e.g. "translate to French", "book a flight".

### 3.6 Transaction log
Brief's columns plus `run_id`, `fee_pct`, `llm_cost_provider`, `quote_accepted`, `model_broker`, `model_buyer`, `sdk_cost_estimate`.

### 3.7 No-copy check
`make check-no-copy` scans this repo for any 6-line window that appears verbatim in any upstream file (the `.upstream/` clones are excluded from the scan of *this* repo but are the corpus to compare against). Part of `make test`.

## 4. Budget projection (to be replaced by smoke measurement)
| Component | Est. per transaction | Basis |
|---|---|---|
| Broker (Sonnet 5.5, ~5 turns, ~30K in / 2K out) | ~$0.08 | pricing table |
| Buyer via_broker (Haiku 5.5) | ~$0.003 | |
| Buyer direct (Haiku 5.5, ~5 turns) | ~$0.006 | |
| decision_gate provider (Haiku 5.5, 1 round) | ~$0.003 | |

Full run 300 x both policies ≈ $28; fee acceptance runs ≈ $8; smoke ≈ $2; phase 3 (20 txns) ≈ $2. Projected total ≈ $40 of the $80 cap. Chaining and long broker turns could double the broker figure; the 20-transaction smoke run decides, and the runner aborts if the projection exceeds the remaining cap.

## 5. Phases and stop points
1. **Phase 1**: registry, SimLedger, meter, 2 stubs, buyer + broker, 5 scenarios, `make smoke` (20 brokered transactions, both policies). Stop and report measured cost per transaction and the projection.
2. **Phase 2**: four real adapters, `make run` (300 brokered + 300 direct + fee acceptance runs), `make report` -> `report/economics.md`.
3. **Phase 3** (only if budget and time remain): `settlement/x402_testnet.py` on Base Sepolia via the public facilitator; 20 transactions; report appendix on handshake latency and friction. Needs funded testnet keys in env (`BENCH_X402_BUYER_KEY`, `BENCH_X402_BROKER_KEY`, `BENCH_X402_PROVIDER_KEY`).

`make setup` creates the venvs (uv, Python 3.12), installs pinned upstreams, seeds the grocery demo DB. `make run` = setup + smoke + full run + report, from a clean clone.

## 6. What I need from you
1. Approval of 3.1 through 3.7, or changes.
2. `ANTHROPIC_API_KEY` for the $100 credit added to this cloud environment's settings as a secret (I will read it under that name). Until it exists I can build and unit-test with a fake model but cannot run the smoke test or measure cost.
3. Confirmation that wrapping model-evidence as a subprocess is fine given it has no license file (it is your repo; nothing is copied either way).
4. Model choice confirmation: Haiku 5.5 buyer, Sonnet 5.5 broker.
