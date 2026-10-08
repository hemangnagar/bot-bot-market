"""Buyer agent (Haiku-class): has a task, a wallet and a policy.

direct: searches the registry itself, pays and invokes providers, orchestrates chains.
via_broker: asks the broker for a quote, accepts or declines, pays the broker.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from registry.client import RegistryClient, WalletClient
from settlement.sim import SimLedger

from .broker import Broker, Quote
from .env import child_env
from .runtime import AgentRun, run_agent
from .tools import FinishState, InvokeState, make_finish_tool, make_invoke_tool, make_search_tool, tool_result

BUYER_DIRECT_SYSTEM = """You are a buyer agent completing one task for your principal by purchasing services from a registry.
You have a wallet and a budget. Be economical: search once with good keywords, pick the cheapest service whose
description and quality note actually cover what the task requires, pay and invoke it, and finish.
If the task needs two steps, invoke them in order and pass data with "$prev.<field>". Use "$task.<field>" to
pass task fields verbatim; never retype long texts. Do not buy what the task does not need.
If no service can do the task, do not pay anything: call finish with success=false and say why.
When done, call finish once with success=true and the required output fields copied from service outputs."""

BUYER_BROKER_SYSTEM = """You are a buyer agent completing one task for your principal through a broker.
The broker receives your task's full fields (text, claims, policy) automatically; you only see their names and sizes,
and that is fine. You never need to read, copy or send the content yourself.
Always call request_quote first, even if you doubt the task is possible: the broker decides what it can do.
If the broker quotes a price within your budget and the plan is plausible for the task, call accept_quote; the broker
executes and returns the output. If the price exceeds your budget, the plan clearly does not fit, or the broker
declines, call decline_quote (skip it if there is no quote).
Then call finish once: success=true with the required output fields copied from the broker's output, otherwise
success=false with a short reason. Do not do anything else."""


@dataclass
class BuyerOutcome:
    policy: str
    finished: bool
    success_claimed: bool
    result: dict[str, Any]
    notes: str
    price_paid_usd: float
    providers: list[str]
    broker_fee_usd: float = 0.0
    quote: Quote | None = None
    quote_accepted: bool | None = None
    execution_error: str = ""
    provider_result: dict[str, Any] = field(default_factory=dict)  # what providers actually returned (judged), vs `result` (what the agent reported)
    agent_run: AgentRun | None = None
    latency_ms: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)


class Buyer:
    def __init__(self, *, registry: RegistryClient, ledger: SimLedger, meter_url: str, api_key: str,
                 model: str = "claude-haiku-5-5", effort: str = "low", max_turns: int = 8, max_budget_usd: float = 0.50):
        # max_budget_usd is a coarse guard only: the CLI prices unrecognised models at a fallback rate, so its estimate
        # can run ~40x above the metered cost for Haiku 5.5. The SpendMeter is the real cap.
        self.registry, self.ledger = registry, ledger
        self.meter_url, self.api_key = meter_url, api_key
        self.model, self.effort, self.max_turns, self.max_budget_usd = model, effort, max_turns, max_budget_usd

    def _prompt(self, task: dict[str, Any]) -> str:
        payload_desc = {k: (f"<string, {len(v)} chars>" if isinstance(v, str) and len(v) > 80 else v) for k, v in task["payload"].items()}
        return (f"Task {task['id']}: {task['description']}\n\nTask fields available as $task.<field>: {json.dumps(payload_desc)}\n"
                f"Required output fields: {task['required_output']}\nBudget: {task['budget_usd']:.2f} USD.")

    async def run_direct(self, task: dict[str, Any], txn: str, wallet: WalletClient) -> BuyerOutcome:
        t0 = time.time()
        inv, fin = InvokeState(task["payload"]), FinishState()
        server = create_sdk_mcp_server("bench", tools=[make_search_tool(self.registry), make_invoke_tool(wallet, inv), make_finish_tool(fin)])
        run = await run_agent(actor="buyer", prompt=self._prompt(task), system_prompt=BUYER_DIRECT_SYSTEM, model=self.model, effort=self.effort,
                              mcp_server=server, allowed_tools=["mcp__bench__search_services", "mcp__bench__invoke_service", "mcp__bench__finish"],
                              max_turns=self.max_turns, max_budget_usd=self.max_budget_usd,
                              env=child_env(actor="buyer", txn=txn, meter_url=self.meter_url, api_key=self.api_key))
        merged: dict[str, Any] = {}
        for c in inv.calls:
            if c.get("ok"):
                merged.update(c.get("output") or {})
        return BuyerOutcome(policy="direct", finished=fin.finished, success_claimed=bool(fin.success), result=fin.result, notes=fin.notes,
                            price_paid_usd=wallet.spent_usd, providers=[c["provider"] for c in inv.calls if c.get("ok")], agent_run=run,
                            latency_ms=int((time.time() - t0) * 1000), calls=[{k: v for k, v in c.items() if k != "output"} for c in inv.calls],
                            provider_result=merged)

    async def run_via_broker(self, task: dict[str, Any], txn: str, wallet: WalletClient, broker: Broker) -> BuyerOutcome:
        t0 = time.time()
        fin = FinishState()
        state: dict[str, Any] = {"quote": None, "accepted": None, "execution": None, "paid": 0.0}

        @tool("request_quote", "Send the task to the broker and get a quote (plan, total price, broker fee) or a decline.",
              {"type": "object", "properties": {"task_summary": {"type": "string"}}, "required": ["task_summary"]})
        async def request_quote(args: dict[str, Any]) -> dict[str, Any]:
            if state["quote"] is not None:
                return tool_result({"error": "you already have a quote"}, is_error=True)
            q = await broker.quote(task, txn)
            state["quote"] = q
            return tool_result(q.for_buyer())

        @tool("accept_quote", "Accept the broker's quote. The broker executes the plan and you pay the total price on success.",
              {"type": "object", "properties": {"quote_id": {"type": "string"}}, "required": ["quote_id"]})
        async def accept_quote(args: dict[str, Any]) -> dict[str, Any]:
            q: Quote | None = state["quote"]
            if q is None or q.declined or args.get("quote_id") != q.quote_id:
                return tool_result({"error": "no such open quote"}, is_error=True)
            if wallet.balance() + 1e-9 < q.total_price_usd:
                return tool_result({"error": "insufficient funds for this quote"}, is_error=True)
            state["accepted"] = True
            ex = broker.execute(q, task)
            state["execution"] = ex
            if not ex.ok:
                return tool_result({"ok": False, "error": f"broker could not complete the plan: {ex.error}", "charged_usd": 0.0}, is_error=True)
            req = self.ledger.quote(broker.wallet.wallet, q.total_price_usd, resource=f"/broker/{q.quote_id}", description="broker service")
            settled = self.ledger.verify_and_settle(self.ledger.sign(wallet.wallet, req, memo=f"quote:{q.quote_id}"), fee_to_broker=q.broker_fee_usd)
            if not settled.success:
                return tool_result({"ok": False, "error": f"payment failed: {settled.error}"}, is_error=True)
            state["paid"] = q.total_price_usd
            wallet.spent_usd += q.total_price_usd
            return tool_result({"ok": True, "paid_usd": round(q.total_price_usd, 4), "tx_id": settled.tx_id, "output": ex.output})

        @tool("decline_quote", "Decline the broker's quote.", {"type": "object", "properties": {"quote_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["reason"]})
        async def decline_quote(args: dict[str, Any]) -> dict[str, Any]:
            state["accepted"] = False
            return tool_result({"ok": True})

        server = create_sdk_mcp_server("bench", tools=[request_quote, accept_quote, decline_quote, make_finish_tool(fin)])
        run = await run_agent(actor="buyer", prompt=self._prompt(task), system_prompt=BUYER_BROKER_SYSTEM, model=self.model, effort=self.effort,
                              mcp_server=server, max_turns=self.max_turns, max_budget_usd=self.max_budget_usd,
                              allowed_tools=["mcp__bench__request_quote", "mcp__bench__accept_quote", "mcp__bench__decline_quote", "mcp__bench__finish"],
                              env=child_env(actor="buyer", txn=txn, meter_url=self.meter_url, api_key=self.api_key))
        q, ex = state["quote"], state["execution"]
        return BuyerOutcome(policy="via_broker", finished=fin.finished, success_claimed=bool(fin.success), result=fin.result, notes=fin.notes,
                            price_paid_usd=state["paid"], providers=list(ex.providers) if ex else [], broker_fee_usd=q.broker_fee_usd if (q and state["paid"]) else 0.0,
                            quote=q, quote_accepted=state["accepted"], execution_error=(ex.error if ex and not ex.ok else ""), agent_run=run,
                            provider_result=(dict(ex.output) if ex and ex.ok and state["paid"] else {}),
                            latency_ms=int((time.time() - t0) * 1000), calls=(ex.steps if ex else []))
