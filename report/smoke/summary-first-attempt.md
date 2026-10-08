# Run smoke-phase1: 20 scenarios, fee 15%

| policy | n | success | mean LLM $/txn | median | max | broker LLM | buyer LLM | mean fee | mean paid | mean latency |
|---|---|---|---|---|---|---|---|---|---|---|
| direct | 20 | 65% | 0.0009 | 0.0008 | 0.0014 | 0.0000 | 0.0009 | 0.0000 | 0.0245 | 7.1s |
| via_broker | 20 | 45% | 0.0090 | 0.0095 | 0.0145 | 0.0082 | 0.0008 | 0.0024 | 0.0139 | 12.5s |

| policy | task type | n | success | mean LLM $/txn |
|---|---|---|---|---|
| direct | adjudicate | 4 | 100% | 0.0006 |
| direct | chain | 5 | 20% | 0.0012 |
| direct | impossible | 3 | 100% | 0.0005 |
| direct | scrub_basic | 4 | 100% | 0.0008 |
| direct | scrub_strict | 4 | 25% | 0.0009 |
| via_broker | adjudicate | 4 | 100% | 0.0093 |
| via_broker | chain | 5 | 0% | 0.0134 |
| via_broker | impossible | 3 | 100% | 0.0024 |
| via_broker | scrub_basic | 4 | 50% | 0.0077 |
| via_broker | scrub_strict | 4 | 0% | 0.0095 |

API spend this run: $0.1967 of cap $80.00

| actor | model | calls | input tok | output tok | cache read | USD |
|---|---|---|---|---|---|---|
| broker | claude-sonnet-5-5 | 52 | 104 | 9070 | 80653 | 0.1644 |
| buyer | claude-haiku-5-5 | 150 | 300 | 40913 | 242705 | 0.0323 |

Projection for 300 scenarios x all policies: $2.95 ($0.0098 per scenario) vs remaining cap $79.80: FITS

Failures:
- smoke-phase1-S002-d direct scrub_strict: pii leaked: first_name,name
- smoke-phase1-S001-v via_broker scrub_basic: agent did not finish
- smoke-phase1-S002-v via_broker scrub_strict: agent reported failure
- smoke-phase1-S004-d direct chain: pii leaked: first_name,name
- smoke-phase1-S006-v via_broker scrub_basic: agent did not finish
- smoke-phase1-S004-v via_broker chain: agent reported failure
- smoke-phase1-S007-d direct scrub_strict: agent reported failure
- smoke-phase1-S007-v via_broker scrub_strict: pii leaked: first_name,name
- smoke-phase1-S009-d direct chain: pii leaked: first_name,name
- smoke-phase1-S009-v via_broker chain: agent reported failure
- smoke-phase1-S012-d direct scrub_strict: pii leaked: first_name,name
- smoke-phase1-S012-v via_broker scrub_strict: agent did not finish
- smoke-phase1-S014-v via_broker chain: agent reported failure
- smoke-phase1-S017-v via_broker scrub_strict: agent did not finish
- smoke-phase1-S019-d direct chain: pii leaked: first_name,name
- smoke-phase1-S020-d direct chain: pii leaked: first_name,name
- smoke-phase1-S019-v via_broker chain: pii leaked: first_name,name
- smoke-phase1-S020-v via_broker chain: agent did not finish
