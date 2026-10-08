# decision_gate provider
Wraps https://github.com/hemangnagar/decision_gate (MIT), pinned to commit
`d22f183267e0c74d813e3de534393e867ec1393c`, installed from its git URL by `pip install -e ".[providers]"`.

Invocation: `decision_gate.runner.run_review(decision=claim_a, context="Counter-position ...: claim_b", builder=P, adversary=P,
max_rounds=1)` where `P` is this adapter's `MeteredClaudeProvider`, an implementation of decision_gate's `Provider` protocol
(`generate_json(system, prompt) -> dict`) that calls Claude Haiku 5.5 through the SpendMeter proxy. The upstream LiteLLM
provider is not used, so litellm is not installed. The verdict is `ledger["commitment"]["action"]`.

This is the only provider that spends LLM tokens; its calls are metered under actor `provider:decision_gate` and logged as
`llm_cost_provider` on each transaction. `max_rounds` defaults to 1 (2 to 3 model calls) and is capped at 2.
Nothing from the upstream repo is copied into this one.
