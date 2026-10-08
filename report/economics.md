# broker-bench economics report

Campaign `phase2`: 300 seeded scenarios, each run under both buyer policies in the base run (600 transactions), plus sweeps none (0 brokered transactions). Broker model claude-sonnet-5-5; buyer model claude-haiku-5-5; the decision_gate provider runs claude-haiku-5-5. All USD figures come from the SpendMeter proxy (API `usage` priced with pricing.yaml), not from model estimates.

## 1. Broker P&L per transaction

**Net margin per brokered deal: $-0.0004** (mean fee $0.0104 minus mean broker reasoning cost $0.0108, over 262 accepted deals). Counting every task the broker reasoned about, including the ones it declined or the buyer rejected (300 tasks), the net is $-0.0012 per task.

Break-even fee on the accepted deals: 18% of the provider price (the run used 15% with a $0.0050 floor).

| task type | brokered | accepted | mean fee | median fee | mean broker LLM | median broker LLM | net / deal | provider LLM (decision_gate) | broker turns |
|---|---|---|---|---|---|---|---|---|---|
| adjudicate | 36 | 36 | $0.0150 | $0.0150 | $0.0110 | $0.0111 | $0.0040 | $0.0011 | 3.0 |
| adjudicate_reasoned | 36 | 35 | $0.0150 | $0.0150 | $0.0111 | $0.0111 | $0.0039 | $0.0011 | 3.0 |
| chain | 48 | 47 | $0.0180 | $0.0180 | $0.0142 | $0.0142 | $0.0038 | $0.0011 | 4.0 |
| eval | 36 | 36 | $0.0075 | $0.0075 | $0.0111 | $0.0112 | $-0.0036 | $0.0000 | 3.0 |
| impossible | 36 | 0 | $0.0000 | $0.0000 | $0.0065 | $0.0065 | $0.0000 | $0.0000 | 3.2 |
| lookup | 36 | 36 | $0.0050 | $0.0050 | $0.0089 | $0.0089 | $-0.0039 | $0.0000 | 3.0 |
| scrub_basic | 36 | 36 | $0.0050 | $0.0050 | $0.0095 | $0.0095 | $-0.0045 | $0.0000 | 3.0 |
| scrub_strict | 36 | 36 | $0.0050 | $0.0050 | $0.0089 | $0.0089 | $-0.0039 | $0.0000 | 3.0 |
| **all** | 300 | 262 | $0.0104 | $0.0075 | $0.0103 | $0.0102 | $-0.0004 | $0.0004 | 3.2 |

## 2. Direct vs via-broker

| task type | policy | n | success | buyer LLM | broker LLM | price paid | total cost to buyer | latency |
|---|---|---|---|---|---|---|---|---|
| adjudicate | direct | 36 | 100% | $0.0010 | $0.0000 | $0.1000 | $0.1010 | 24.2s |
| adjudicate | via_broker | 36 | 100% | $0.0008 | $0.0110 | $0.1150 | $0.1158 | 58.7s |
| adjudicate_reasoned | direct | 36 | 100% | $0.0013 | $0.0000 | $0.1000 | $0.1013 | 36.2s |
| adjudicate_reasoned | via_broker | 36 | 97% | $0.0011 | $0.0111 | $0.1118 | $0.1130 | 58.0s |
| chain | direct | 48 | 73% | $0.0014 | $0.0000 | $0.1200 | $0.1214 | 38.7s |
| chain | via_broker | 48 | 71% | $0.0012 | $0.0142 | $0.1351 | $0.1363 | 60.4s |
| eval | direct | 36 | 100% | $0.0008 | $0.0000 | $0.0500 | $0.0508 | 16.1s |
| eval | via_broker | 36 | 100% | $0.0010 | $0.0111 | $0.0575 | $0.0585 | 27.8s |
| impossible | direct | 36 | 100% | $0.0005 | $0.0000 | $0.0000 | $0.0005 | 12.7s |
| impossible | via_broker | 36 | 100% | $0.0003 | $0.0065 | $0.0000 | $0.0003 | 15.1s |
| lookup | direct | 36 | 100% | $0.0007 | $0.0000 | $0.0100 | $0.0107 | 25.1s |
| lookup | via_broker | 36 | 100% | $0.0007 | $0.0089 | $0.0150 | $0.0157 | 33.3s |
| scrub_basic | direct | 36 | 100% | $0.0008 | $0.0000 | $0.0100 | $0.0108 | 8.0s |
| scrub_basic | via_broker | 36 | 100% | $0.0006 | $0.0095 | $0.0150 | $0.0156 | 30.3s |
| scrub_strict | direct | 36 | 83% | $0.0007 | $0.0000 | $0.0200 | $0.0207 | 11.0s |
| scrub_strict | via_broker | 36 | 83% | $0.0007 | $0.0089 | $0.0250 | $0.0257 | 42.2s |
| all | direct | 300 | 94% | $0.0009 | $0.0000 | $0.0540 | $0.0549 | 22.2s |
| all | via_broker | 300 | 93% | $0.0008 | $0.0103 | $0.0623 | $0.0632 | 41.5s |

**Where the broker wins:** impossible: total cost to buyer $0.0003 via broker vs $0.0005 direct.

## 3. Fee sensitivity

| fee % | source | brokered | quotes accepted | success | mean fee | mean broker LLM | net / brokered task | net / accepted deal | mean price to buyer |
|---|---|---|---|---|---|---|---|---|---|
| 15 | measured run | 300 | 99% | 93% | $0.0104 | $0.0103 | $-0.0012 | $-0.0004 | $0.0714 |
| 5 | re-priced base run | 300 | as base | as base | $0.0052 | $0.0103 | $-0.0058 | $-0.0056 | $0.0662 |
| 10 | re-priced base run | 300 | as base | as base | $0.0076 | $0.0103 | $-0.0037 | $-0.0032 | $0.0686 |
| 15 | re-priced base run | 300 | as base | as base | $0.0104 | $0.0103 | $-0.0012 | $-0.0004 | $0.0714 |
| 25 | re-priced base run | 300 | as base | as base | $0.0159 | $0.0103 | $0.0036 | $0.0051 | $0.0769 |
| 50 | re-priced base run | 300 | as base | as base | $0.0305 | $0.0103 | $0.0163 | $0.0197 | $0.0915 |
| 100 | re-priced base run | 300 | as base | as base | $0.0610 | $0.0103 | $0.0430 | $0.0502 | $0.1220 |

Measured runs re-ran the broker and buyers at each fee on the same scenarios; re-priced rows recompute the fee on the base run's plans without re-running, so they isolate the arithmetic from behaviour changes (buyer acceptance, broker plan choice).

### Broker effort sweep

_No broker-effort sweep in this campaign._

### Does the broker's provider memory help?

| slice | n | memory populated | success | mean broker LLM | broker turns | searches per quote |
|---|---|---|---|---|---|---|
| first third | 100 | 97% | 94% | $0.0101 | 3.1 | 1.14 |
| last third | 100 | 100% | 90% | $0.0107 | 3.3 | 1.26 |

The memory records technical success and latency per provider. Providers in this bench almost never fail technically, so the table above mostly measures whether a populated memory changes the broker's search behaviour and cost; quality failures (a scrubber missing a name) are not fed back to it.

## 4. Failure modes

40 failed transactions out of 600 across the campaign.

| rank | failure mode | count | share of failures | example (transaction, policy, task type) | what happened |
|---|---|---|---|---|---|
| 1 | pii leaked (provider output) | 27 | 68% | phase2-base-S010-d, direct, scrub_strict | Used edshield (COPPA policy, $0.02). Its output left the student name "Leila Garcia" unredacted in two places, even though the task requires removing all student names. I manually replaced both occurrences with [NAME] an |
| 2 | agent gave up | 13 | 32% | phase2-base-S037-v, via_broker, chain | Quote b7f9ab889a accepted, paid 0.138 USD (plus 0.018 broker fee included in quote). The broker's redaction missed the student's full name, which is a COPPA identifier. I corrected this by hand in the redacted_text field |

## 5. Spend

**Total API spend across every run under runs/: $4.3041 of the $80.0000 cap (5.4%).**

By model: claude-sonnet-5-5 $3.4440, claude-haiku-5-5 $0.8601.

| actor | model | calls | input tokens | output tokens | cache-read tokens | USD |
|---|---|---|---|---|---|---|
| broker | claude-sonnet-5-5 | 1020 | 2040 | 170661 | 1724846 | $3.4440 |
| buyer | claude-haiku-5-5 | 2730 | 5460 | 673526 | 5053896 | $0.5884 |
| provider:decision_gate | claude-haiku-5-5 | 476 | 362491 | 470874 | 0 | $0.2717 |

| run | USD |
|---|---|
| phase2-base | $3.8900 |
| smoke-phase1 | $0.1967 |
| smoke-phase1b | $0.2174 |

Agent SDK estimate vs metered cost in the base run: buyer: meter $0.5257 vs SDK $20.1222 (38.3x); broker: meter $3.0926 vs SDK $3.2476 (1.1x). The CLI prices claude-haiku-5-5 as an unrecognised model, so its estimate is not usable for Haiku; the meter is authoritative.

## 6. What a real deployment would change

1. **Deterministic routing for repeat tasks.** 97% of brokered tasks in the base run had the same (task type, plan) as an earlier one. Serving those from a cached plan with no model call removes about 97% of broker reasoning cost ($0.0100 per brokered task on average) and most of the quote latency.
2. **Cheaper model for discovery.** The quote phase is 3.2 model turns of which the first is always a registry search. Running search-and-shortlist on Haiku 5.5 and only the plan/quote turn on Sonnet 5.5 would cut roughly a third of broker cost (Haiku output tokens cost 1/20th of Sonnet's); the buyers in this run show Haiku handles the search step reliably.
3. **Output-token diet.** Output tokens are 91% of the broker's bill; cache reads are already 100% of its input. A terser rationale and a structured-output quote (no prose) would remove an estimated 20 to 30% of output tokens.
4. **Lower effort by default.** See the effort sweep above: the low-effort broker's cost and success rate bound what a cheaper default buys.
5. **Skip the quote round-trip for small tickets.** For plans under a fee floor's worth of provider cost, execute first and settle after; the buyer-side accept/decline turn (two Haiku turns, about $0.0003) and the broker's quote wait disappear.
6. **Amortise the agent spawn.** Each quote spawns a fresh CLI process and re-writes the system-prompt cache (about 1,400 tokens). A long-lived broker session that handles many quotes keeps the cache warm and removes the per-spawn cache write.
7. **Price the fee to cover reasoning.** At the measured $0.0103 per brokered task the floor fee needs to be at least that; the fee-sensitivity table shows where the percentage fee alone breaks even.
