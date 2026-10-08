"""Broker agent: the product under test.

Quote phase (LLM, Sonnet-class): discover candidates, score on price / schema fit /
latency / past success, optionally chain, and submit a quote or decline.
Execute phase (deterministic): run the accepted plan, paying providers from the
broker wallet, and record provider outcomes in the broker's memory.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from registry.client import ProviderError, RegistryClient, WalletClient

from .env import child_env
from .runtime import AgentRun, run_agent
from .tools import make_search_tool, substitute, tool_result

BROKER_SYSTEM = """You are a service broker for other software agents. A buyer sends you a task; you find the
services in a registry that can do it, choose the best plan on price, fit, latency and track record, and quote.
You pay the providers yourself and charge the buyer a fee on top, so a plan that fails costs you money.

Rules:
- Call search_services once or twice with good keywords; do not loop.
- A plan is an ordered list of steps {provider, payload}. Use "$task.<field>" to pass a task field verbatim and
  "$prev.<field>" to pass a field of the previous step's output. Never copy long texts into payloads yourself.
- Match each provider's required input fields exactly; chain steps only when the task needs it.
- If no registered service fits the task, call decline with a short reason. Do not quote for work you cannot do.
- When you have a plan, call submit_quote once with a one-line rationale. Then stop."""


@dataclass
class Quote:
    quote_id: str
    task_id: str
    plan: list[dict[str, Any]]
    provider_total_usd: float
    broker_fee_usd: float
    total_price_usd: float
    rationale: str
    declined: bool = False
    decline_reason: str = ""
    memory_used: bool = False
    agent_run: AgentRun | None = None

    def for_buyer(self) -> dict[str, Any]:
        if self.declined:
            return {"declined": True, "reason": self.decline_reason}
        return {"quote_id": self.quote_id, "plan": [{"provider": s["provider"]} for s in self.plan],
                "total_price_usd": round(self.total_price_usd, 4), "broker_fee_usd": round(self.broker_fee_usd, 4),
                "rationale": self.rationale}


@dataclass
class Execution:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    provider_cost_usd: float = 0.0
    error: str = ""


class Broker:
    def __init__(self, *, registry: RegistryClient, wallet: WalletClient, meter_url: str, api_key: str,
                 model: str = "claude-sonnet-5-5", effort: str = "medium", fee_pct: float = 15.0, fee_floor_usd: float = 0.005,
                 max_turns: int = 8, max_budget_usd: float = 1.50, use_memory: bool = True):
        self.registry, self.wallet = registry, wallet
        self.meter_url, self.api_key = meter_url, api_key
        self.model, self.effort = model, effort
        self.fee_pct, self.fee_floor_usd = fee_pct, fee_floor_usd
        self.max_turns, self.max_budget_usd = max_turns, max_budget_usd
        self.use_memory = use_memory
        self.memory: dict[str, dict[str, float]] = {}  # provider -> {calls, successes, latency_sum}
        self.quotes: dict[str, Quote] = {}

    # -- memory -------------------------------------------------------------
    def remember(self, provider: str, ok: bool, latency_ms: int) -> None:
        m = self.memory.setdefault(provider, {"calls": 0, "successes": 0, "latency_sum": 0})
        m["calls"] += 1
        m["successes"] += int(ok)
        m["latency_sum"] += latency_ms

    def memory_table(self) -> str:
        if not self.memory:
            return "(no history yet)"
        rows = [f"- {p}: {int(m['calls'])} calls, {m['successes'] / m['calls']:.0%} success, {m['latency_sum'] / m['calls']:.0f} ms avg"
                for p, m in sorted(self.memory.items())]
        return "\n".join(rows)

    # -- pricing ------------------------------------------------------------
    def price_plan(self, plan: list[dict[str, Any]]) -> tuple[float, float, float]:
        total = 0.0
        for step in plan:
            total += float(self.registry.card(step["provider"])["price_usd"])
        fee = max(total * self.fee_pct / 100.0, self.fee_floor_usd)
        return total, fee, total + fee

    # -- quote phase (LLM) --------------------------------------------------
    async def quote(self, task: dict[str, Any], txn: str) -> Quote:
        quote_id = uuid.uuid4().hex[:10]
        holder: dict[str, Any] = {}
        searches: list[str] = []

        @tool("submit_quote", "Submit the plan you will execute. Prices are looked up from the registry; your fee is added automatically.",
              {"type": "object", "properties": {
                  "plan": {"type": "array", "items": {"type": "object", "properties": {"provider": {"type": "string"}, "payload": {"type": "object"}},
                                                     "required": ["provider", "payload"]}},
                  "rationale": {"type": "string"}}, "required": ["plan", "rationale"]})
        async def submit_quote(args: dict[str, Any]) -> dict[str, Any]:
            plan = list(args.get("plan") or [])
            if not plan:
                return tool_result({"error": "plan is empty; call decline if nothing fits"}, is_error=True)
            try:
                for step in plan:
                    card = self.registry.card(step["provider"])
                    missing = [k for k in card.get("input_schema", {}).get("required", []) if k not in (step.get("payload") or {})]
                    if missing:
                        return tool_result({"error": f"{step['provider']} requires fields {missing}"}, is_error=True)
                total, fee, price = self.price_plan(plan)
            except Exception as exc:  # noqa: BLE001
                return tool_result({"error": f"invalid plan: {exc}"}, is_error=True)
            holder["quote"] = Quote(quote_id=quote_id, task_id=task["id"], plan=plan, provider_total_usd=total, broker_fee_usd=fee,
                                    total_price_usd=price, rationale=str(args.get("rationale", "")), memory_used=bool(self.memory))
            return tool_result({"ok": True, "quote_id": quote_id, "provider_total_usd": round(total, 4), "broker_fee_usd": round(fee, 4),
                                "total_price_usd": round(price, 4), "message": "quote recorded; stop now"})

        @tool("decline", "Decline the task because no registered service can do it.",
              {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]})
        async def decline(args: dict[str, Any]) -> dict[str, Any]:
            holder["quote"] = Quote(quote_id=quote_id, task_id=task["id"], plan=[], provider_total_usd=0, broker_fee_usd=0, total_price_usd=0,
                                    rationale="", declined=True, decline_reason=str(args.get("reason", "")), memory_used=bool(self.memory))
            return tool_result({"ok": True, "message": "declined; stop now"})

        server = create_sdk_mcp_server("bench", tools=[make_search_tool(self.registry, searches), submit_quote, decline])
        payload_desc = {k: (f"<string, {len(v)} chars>" if isinstance(v, str) and len(v) > 80 else v) for k, v in task["payload"].items()}
        memory = self.memory_table() if self.use_memory else "(memory disabled)"
        prompt = (f"Task {task['id']}: {task['description']}\n\nTask fields available as $task.<field>: {json.dumps(payload_desc)}\n"
                  f"Required output fields: {task['required_output']}\n\nYour provider track record this run:\n{memory}\n\n"
                  f"Find a plan and submit a quote, or decline.")
        run = await run_agent(actor="broker", prompt=prompt, system_prompt=BROKER_SYSTEM, model=self.model, effort=self.effort,
                              mcp_server=server, allowed_tools=["mcp__bench__search_services", "mcp__bench__submit_quote", "mcp__bench__decline"],
                              max_turns=self.max_turns, max_budget_usd=self.max_budget_usd,
                              env=child_env(actor="broker", txn=txn, meter_url=self.meter_url, api_key=self.api_key))
        q = holder.get("quote")
        if q is None:
            q = Quote(quote_id=quote_id, task_id=task["id"], plan=[], provider_total_usd=0, broker_fee_usd=0, total_price_usd=0, rationale="",
                      declined=True, decline_reason=f"broker produced no quote ({run.terminal_reason}{': ' + run.error if run.error else ''})",
                      memory_used=bool(self.memory))
        q.agent_run = run
        self.quotes[quote_id] = q
        return q

    # -- execute phase (deterministic) -------------------------------------
    def execute(self, quote: Quote, task: dict[str, Any], txn: str = "") -> Execution:
        ex = Execution(ok=True)
        prev: dict[str, Any] | None = None
        merged: dict[str, Any] = {}
        for step in quote.plan:
            name = step["provider"]
            try:
                payload = substitute(step.get("payload") or {}, task["payload"], prev)
                res = self.wallet.invoke(name, payload, memo=f"broker:{quote.quote_id}", txn=txn)
            except (KeyError, ProviderError) as exc:
                ex.ok, ex.error = False, f"{name}: {exc}"
                ex.steps.append({"provider": name, "ok": False, "error": str(exc)})
                self.remember(name, False, 0)
                break
            prev = res.output
            merged.update(res.output)
            ex.steps.append({"provider": name, "ok": True, "price_paid_usd": res.price_paid_usd, "latency_ms": res.latency_ms, "tx_id": res.tx_id})
            ex.providers.append(name)
            ex.provider_cost_usd += res.price_paid_usd
            self.remember(name, True, res.latency_ms)
        ex.output = merged
        return ex
