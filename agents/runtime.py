"""Thin wrapper over claude_agent_sdk.query that captures the result and tool calls."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, ToolUseBlock, query


@dataclass
class AgentRun:
    actor: str
    model: str
    result: ResultMessage | None = None
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    stderr: list[str] = field(default_factory=list)

    @property
    def sdk_cost_usd(self) -> float:
        return float(self.result.total_cost_usd or 0.0) if self.result else 0.0

    @property
    def num_turns(self) -> int:
        return int(self.result.num_turns) if self.result else 0

    @property
    def terminal_reason(self) -> str:
        if self.error:
            return "error"
        if self.result is None:
            return "no_result"
        return str(self.result.terminal_reason or self.result.subtype)


async def run_agent(*, actor: str, prompt: str, system_prompt: str, model: str, effort: str, mcp_server: Any,
                    allowed_tools: list[str], max_turns: int, max_budget_usd: float, env: dict[str, str]) -> AgentRun:
    run = AgentRun(actor=actor, model=model)
    options = ClaudeAgentOptions(
        model=model, system_prompt=system_prompt, effort=effort, tools=[], mcp_servers={"bench": mcp_server},
        allowed_tools=allowed_tools, max_turns=max_turns, max_budget_usd=max_budget_usd, env=env, permission_mode="dontAsk",
        stderr=lambda line: run.stderr.append(line) if len(run.stderr) < 50 else None,
    )
    t0 = time.time()
    try:
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        run.tool_calls.append(block.name.replace("mcp__bench__", ""))
            elif isinstance(msg, ResultMessage):
                run.result = msg
                if msg.is_error and not run.error:
                    run.error = (msg.errors[0] if msg.errors else msg.result or msg.subtype)[:300]
    except Exception as exc:  # noqa: BLE001 - the transaction log records it as a failure
        run.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    run.duration_ms = int((time.time() - t0) * 1000)
    return run
