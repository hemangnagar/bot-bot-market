# Run smoke-phase1b: 20 scenarios, fee 15%

| policy | n | success | mean LLM $/txn | median | max | broker LLM | buyer LLM | mean fee | mean paid | mean latency |
|---|---|---|---|---|---|---|---|---|---|---|
| direct | 20 | 95% | 0.0008 | 0.0007 | 0.0017 | 0.0000 | 0.0008 | 0.0000 | 0.0245 | 6.3s |
| via_broker | 20 | 95% | 0.0100 | 0.0094 | 0.0143 | 0.0093 | 0.0007 | 0.0049 | 0.0294 | 12.5s |

| policy | task type | n | success | mean LLM $/txn |
|---|---|---|---|---|
| direct | adjudicate | 4 | 100% | 0.0005 |
| direct | chain | 5 | 100% | 0.0013 |
| direct | impossible | 3 | 100% | 0.0006 |
| direct | scrub_basic | 4 | 100% | 0.0008 |
| direct | scrub_strict | 4 | 75% | 0.0008 |
| via_broker | adjudicate | 4 | 100% | 0.0089 |
| via_broker | chain | 5 | 100% | 0.0135 |
| via_broker | impossible | 3 | 100% | 0.0066 |
| via_broker | scrub_basic | 4 | 100% | 0.0099 |
| via_broker | scrub_strict | 4 | 75% | 0.0095 |

API spend this run: $0.2174 of cap $80.00

| actor | model | calls | input tok | output tok | cache read | USD |
|---|---|---|---|---|---|---|
| broker | claude-sonnet-5-5 | 61 | 122 | 10025 | 94517 | 0.1869 |
| buyer | claude-haiku-5-5 | 165 | 330 | 36830 | 283973 | 0.0305 |

Projection for 300 scenarios x all policies: $3.26 ($0.0109 per scenario) vs remaining cap $79.78: FITS

Failures:
- smoke-phase1b-S007-d direct scrub_strict: pii leaked: first_name,name
- smoke-phase1b-S007-v via_broker scrub_strict: pii leaked: first_name,name
