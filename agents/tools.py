"""In-process MCP tools shared by buyer and broker, plus plan-payload templating."""
from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from registry.client import ProviderError, RegistryClient, WalletClient


def tool_result(obj: Any, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)}]}
    if is_error:
        out["is_error"] = True
    return out


def substitute(value: Any, task: dict[str, Any], prev: dict[str, Any] | None) -> Any:
    """Replace "$task.<field>" / "$prev.<field>" strings so long texts never round-trip through the model."""
    if isinstance(value, str):
        if value.startswith("$task."):
            key = value[6:]
            if key not in task:
                raise KeyError(f"task has no field {key!r}")
            return task[key]
        if value.startswith("$prev."):
            key = value[6:]
            if not prev or key not in prev:
                raise KeyError(f"previous output has no field {key!r}")
            return prev[key]
        return value
    if isinstance(value, dict):
        return {k: substitute(v, task, prev) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, task, prev) for v in value]
    return value


def compact_card(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": c["name"], "description": c["description"], "price_usd": c["price_usd"], "latency_p50_ms": c["latency_p50_ms"],
        "quality_note": c.get("quality_note", ""), "input_required": c.get("input_schema", {}).get("required", []),
        "input_fields": sorted((c.get("input_schema", {}).get("properties") or {}).keys()),
        "output_fields": sorted((c.get("output_schema", {}).get("properties") or {}).keys()),
    }


def make_search_tool(registry: RegistryClient, log: list[str] | None = None):
    @tool("search_services", "Search the service registry by keywords. Returns capability cards with price and input/output fields.",
          {"type": "object", "properties": {"query": {"type": "string", "description": "keywords describing the capability"}}, "required": ["query"]})
    async def search_services(args: dict[str, Any]) -> dict[str, Any]:
        q = str(args.get("query", ""))
        if log is not None:
            log.append(q)
        cards = registry.search(q)
        if not cards:
            return tool_result({"results": [], "note": "no provider matched these keywords"})
        return tool_result({"results": [compact_card(c) for c in cards]})

    return search_services


class InvokeState:
    def __init__(self, task_payload: dict[str, Any]):
        self.task = task_payload
        self.prev: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []


def make_invoke_tool(wallet: WalletClient, state: InvokeState):
    @tool("invoke_service",
          "Pay for and call one service. `payload` must match the service's input fields. A string value \"$task.<field>\" is replaced "
          "with that field of your task; \"$prev.<field>\" with that field of the previous service's output. Returns the output and price paid.",
          {"type": "object", "properties": {"name": {"type": "string"}, "payload": {"type": "object"}}, "required": ["name", "payload"]})
    async def invoke_service(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name"))
        try:
            payload = substitute(args.get("payload") or {}, state.task, state.prev)
            res = wallet.invoke(name, payload)
        except KeyError as exc:
            return tool_result({"error": f"bad reference: {exc}"}, is_error=True)
        except ProviderError as exc:
            state.calls.append({"provider": name, "ok": False, "error": exc.detail})
            return tool_result({"error": str(exc)}, is_error=True)
        state.prev = res.output
        state.calls.append({"provider": name, "ok": True, "price_paid_usd": res.price_paid_usd, "latency_ms": res.latency_ms, "output": res.output})
        return tool_result({"provider": name, "price_paid_usd": res.price_paid_usd, "wallet_balance_usd": round(wallet.balance(), 4), "output": res.output})

    return invoke_service


class FinishState:
    def __init__(self) -> None:
        self.finished = False
        self.success: bool | None = None
        self.result: dict[str, Any] = {}
        self.notes = ""


def make_finish_tool(state: FinishState):
    @tool("finish", "Report the final outcome and stop. Call exactly once when the task is done, or when it cannot be done.",
          {"type": "object", "properties": {
              "success": {"type": "boolean", "description": "true if the task's required output was obtained"},
              "result": {"type": "object", "description": "the required output fields, copied from service outputs"},
              "notes": {"type": "string"}}, "required": ["success", "result"]})
    async def finish(args: dict[str, Any]) -> dict[str, Any]:
        state.finished = True
        state.success = bool(args.get("success"))
        state.result = dict(args.get("result") or {})
        state.notes = str(args.get("notes") or "")
        return tool_result({"ok": True, "message": "recorded; you are done"})

    return finish
