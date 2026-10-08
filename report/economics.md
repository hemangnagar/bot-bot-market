# broker-bench economics report

Campaign `smoke-phase1b`: 20 seeded scenarios, each run under both buyer policies in the base run (40 transactions), plus sweeps none (0 brokered transactions). Broker model claude-sonnet-5-5; buyer model claude-haiku-5-5; the decision_gate provider runs claude-haiku-5-5. All USD figures come from the SpendMeter proxy (API `usage` priced with pricing.yaml), not from model estimates.

## 1. Broker P&L per transaction

**Net margin per brokered deal: $-0.0042** (mean fee $0.0057 minus mean broker reasoning cost $0.0099, over 17 accepted deals). Counting every task the broker reasoned about, including the ones it declined or the buyer rejected (20 tasks), the net is $-0.0045 per task.

Break-even fee on the accepted deals: 34% of the provider price (the run used 15% with a $0.0050 floor).

| task type | brokered | accepted | mean fee | median fee | mean broker LLM | median broker LLM | net / deal | provider LLM (decision_gate) | broker turns |
|---|---|---|---|---|---|---|---|---|---|
| adjudicate | 4 | 4 | $0.0050 | $0.0050 | $0.0083 | $0.0083 | $-0.0033 | $0.0000 | 3.0 |
| chain | 5 | 5 | $0.0075 | $0.0075 | $0.0126 | $0.0123 | $-0.0051 | $0.0000 | 4.0 |
| impossible | 3 | 0 | $0.0000 | $0.0000 | $0.0062 | $0.0063 | $0.0000 | $0.0000 | 3.3 |
| scrub_basic | 4 | 4 | $0.0050 | $0.0050 | $0.0093 | $0.0094 | $-0.0043 | $0.0000 | 3.0 |
| scrub_strict | 4 | 4 | $0.0050 | $0.0050 | $0.0087 | $0.0087 | $-0.0037 | $0.0000 | 3.0 |
| **all** | 20 | 17 | $0.0057 | $0.0050 | $0.0093 | $0.0088 | $-0.0042 | $0.0000 | 3.3 |

## 2. Direct vs via-broker

| task type | policy | n | success | buyer LLM | broker LLM | price paid | total cost to buyer | latency |
|---|---|---|---|---|---|---|---|---|
| adjudicate | direct | 4 | 100% | $0.0005 | $0.0000 | $0.0300 | $0.0305 | 4.9s |
| adjudicate | via_broker | 4 | 100% | $0.0006 | $0.0083 | $0.0350 | $0.0356 | 11.1s |
| chain | direct | 5 | 100% | $0.0013 | $0.0000 | $0.0500 | $0.0513 | 8.8s |
| chain | via_broker | 5 | 100% | $0.0009 | $0.0126 | $0.0575 | $0.0584 | 15.4s |
| impossible | direct | 3 | 100% | $0.0006 | $0.0000 | $0.0000 | $0.0006 | 4.6s |
| impossible | via_broker | 3 | 100% | $0.0003 | $0.0062 | $0.0000 | $0.0003 | 8.8s |
| scrub_basic | direct | 4 | 100% | $0.0008 | $0.0000 | $0.0100 | $0.0108 | 5.9s |
| scrub_basic | via_broker | 4 | 100% | $0.0006 | $0.0093 | $0.0150 | $0.0156 | 11.5s |
| scrub_strict | direct | 4 | 75% | $0.0008 | $0.0000 | $0.0200 | $0.0208 | 6.2s |
| scrub_strict | via_broker | 4 | 75% | $0.0008 | $0.0087 | $0.0250 | $0.0258 | 14.0s |
| all | direct | 20 | 95% | $0.0008 | $0.0000 | $0.0245 | $0.0253 | 6.3s |
| all | via_broker | 20 | 95% | $0.0007 | $0.0093 | $0.0294 | $0.0301 | 12.5s |

**Where the broker wins:** impossible: total cost to buyer $0.0003 via broker vs $0.0006 direct.

## 3. Fee sensitivity

| fee % | source | brokered | quotes accepted | success | mean fee | mean broker LLM | net / brokered task | net / accepted deal | mean price to buyer |
|---|---|---|---|---|---|---|---|---|---|
| 15 | measured run | 20 | 100% | 95% | $0.0057 | $0.0093 | $-0.0045 | $-0.0042 | $0.0346 |
| 5 | re-priced base run | 20 | as base | as base | $0.0050 | $0.0093 | $-0.0051 | $-0.0049 | $0.0338 |
| 10 | re-priced base run | 20 | as base | as base | $0.0050 | $0.0093 | $-0.0051 | $-0.0049 | $0.0338 |
| 15 | re-priced base run | 20 | as base | as base | $0.0057 | $0.0093 | $-0.0045 | $-0.0042 | $0.0346 |
| 25 | re-priced base run | 20 | as base | as base | $0.0078 | $0.0093 | $-0.0027 | $-0.0021 | $0.0366 |
| 50 | re-priced base run | 20 | as base | as base | $0.0144 | $0.0093 | $0.0029 | $0.0045 | $0.0432 |
| 100 | re-priced base run | 20 | as base | as base | $0.0288 | $0.0093 | $0.0152 | $0.0189 | $0.0576 |

Measured runs re-ran the broker and buyers at each fee on the same scenarios; re-priced rows recompute the fee on the base run's plans without re-running, so they isolate the arithmetic from behaviour changes (buyer acceptance, broker plan choice).

### Broker effort sweep

_No broker-effort sweep in this campaign._

### Does the broker's provider memory help?

_Too few brokered transactions to compare early vs late._

The memory records technical success and latency per provider. Providers in this bench almost never fail technically, so the table above mostly measures whether a populated memory changes the broker's search behaviour and cost; quality failures (a scrubber missing a name) are not fed back to it.

## 4. Failure modes

2 failed transactions out of 40 across the campaign.

| rank | failure mode | count | share of failures | example (transaction, policy, task type) | what happened |
|---|---|---|---|---|---|
| 1 | pii leaked (provider output) | 2 | 100% | smoke-phase1b-S007-d, direct, scrub_strict | Used edshield ($0.02, COPPA policy). The service's output left the student name "Sofia Patel" unredacted, twice (once in the header, once as "Sofia"). I manually replaced both with [STUDENT_NAME] and adjusted entity_coun |

## 5. Spend

**Total API spend across every run under runs/: $0.5630 of the $80.0000 cap (0.7%).**

By model: claude-sonnet-5-5 $0.4688, claude-haiku-5-5 $0.0941.

| actor | model | calls | input tokens | output tokens | cache-read tokens | USD |
|---|---|---|---|---|---|---|
| broker | claude-sonnet-5-5 | 149 | 298 | 24877 | 232203 | $0.4688 |
| buyer | claude-haiku-5-5 | 410 | 820 | 102802 | 695542 | $0.0843 |
| provider:decision_gate | claude-haiku-5-5 | 17 | 12333 | 17276 | 0 | $0.0099 |

| run | USD |
|---|---|
| phase2-base | $0.1489 |
| smoke-phase1 | $0.1967 |
| smoke-phase1b | $0.2174 |

Agent SDK estimate vs metered cost in the base run: buyer: meter $0.0305 vs SDK $1.1618 (38.1x); broker: meter $0.1869 vs SDK $0.1964 (1.1x). The CLI prices claude-haiku-5-5 as an unrecognised model, so its estimate is not usable for Haiku; the meter is authoritative.

## 6. What a real deployment would change

1. **Deterministic routing for repeat tasks.** 75% of brokered tasks in the base run had the same (task type, plan) as an earlier one. Serving those from a cached plan with no model call removes about 75% of broker reasoning cost ($0.0070 per brokered task on average) and most of the quote latency.
2. **Cheaper model for discovery.** The quote phase is 3.3 model turns of which the first is always a registry search. Running search-and-shortlist on Haiku 5.5 and only the plan/quote turn on Sonnet 5.5 would cut roughly a third of broker cost (Haiku output tokens cost 1/20th of Sonnet's); the buyers in this run show Haiku handles the search step reliably.
3. **Output-token diet.** Output tokens are 91% of the broker's bill; cache reads are already 100% of its input. A terser rationale and a structured-output quote (no prose) would remove an estimated 20 to 30% of output tokens.
4. **Lower effort by default.** See the effort sweep above: the low-effort broker's cost and success rate bound what a cheaper default buys.
5. **Skip the quote round-trip for small tickets.** For plans under a fee floor's worth of provider cost, execute first and settle after; the buyer-side accept/decline turn (two Haiku turns, about $0.0003) and the broker's quote wait disappear.
6. **Amortise the agent spawn.** Each quote spawns a fresh CLI process and re-writes the system-prompt cache (about 1,400 tokens). A long-lived broker session that handles many quotes keeps the cache warm and removes the per-spawn cache write.
7. **Price the fee to cover reasoning.** At the measured $0.0093 per brokered task the floor fee needs to be at least that; the fee-sensitivity table shows where the percentage fee alone breaks even.
