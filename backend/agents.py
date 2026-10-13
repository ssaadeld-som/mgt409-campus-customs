"""Five-role PydanticAI ticket team that calls trusted Campus Customs MCP tools."""
from __future__ import annotations

import asyncio
import json
import os
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import Client
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

try:
    from backend.audit import append_event
    from backend.models import SpecialistReport, TeamRunResult, TicketResolution, TicketRunResult, TicketStatus
    from mcp_server.server import mcp
except ModuleNotFoundError:
    from .audit import append_event
    from .models import SpecialistReport, TeamRunResult, TicketResolution, TicketRunResult, TicketStatus
    from mcp_server.server import mcp

HOMEWORK_ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = HOMEWORK_ROOT.parent
PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"
MODEL_NAME = "gpt-6-luna"
MAX_TOOL_CALLS_PER_AGENT = 12
MAX_BOSS_DELEGATIONS = 4
MAX_TICKETS_PER_BATCH = 10
MAX_OUTPUT_RETRIES = 1

load_dotenv(COURSE_ROOT / ".env")
load_dotenv(HOMEWORK_ROOT / ".env")

CURRENT_AGENT: ContextVar[str] = ContextVar("current_agent", default="Orchestrator")
CURRENT_TICKET: ContextVar[int | None] = ContextVar("current_ticket", default=None)
TICKET_RUN_STATE: ContextVar[dict[str, Any] | None] = ContextVar("ticket_run_state", default=None)
DELEGATION_COUNT: ContextVar[list[int] | None] = ContextVar("delegation_count", default=None)


def _mcp_result_content(result: Any) -> Any:
    if getattr(result, "is_error", False):
        details = " ".join(getattr(item, "text", "") for item in getattr(result, "content", []))
        raise RuntimeError(details or "MCP tool returned an error.")
    value = getattr(result, "structured_content", None)
    if isinstance(value, dict) and set(value) == {"result"}:
        return value["result"]
    if value is not None:
        return value
    return [getattr(item, "text", str(item)) for item in getattr(result, "content", [])]


async def call_mcp(tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an actual registered FastMCP tool and log concise start/result events."""
    role = CURRENT_AGENT.get()
    args = arguments or {}
    current_ticket = CURRENT_TICKET.get()
    requested_ticket = args.get("ticket_id")
    if current_ticket is not None and requested_ticket is not None and requested_ticket != current_ticket:
        reason = f"Agent is scoped to ticket {current_ticket}; refusing ticket {requested_ticket}."
        append_event(role, "tool_scope_refused", tool_name, args, reason, "ticket_scope_blocked")
        raise ValueError(reason)
    run_state = TICKET_RUN_STATE.get()
    if role == "Boss" and run_state and run_state.get("resolved_by_agent") and tool_name != "resolve_ticket":
        reason = "resolve_ticket has already been called; it must be the Boss's final tool action for this ticket."
        append_event(role, "post_resolution_tool_refused", tool_name, args, reason, "resolution_was_final")
        raise ValueError(reason)
    append_event(role, "mcp_tool_started", tool_name, args, "")
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(tool_name, args)
        output = _mcp_result_content(result)
    except Exception as exc:
        append_event(role, "mcp_tool_error", tool_name, args, f"{type(exc).__name__}: {exc}", "tool_error")
        # A malformed/read-only SELECT should be visible to the agent as data,
        # but it should not abort the whole ticket run. This lets the agent use
        # the exact SQLite error, avoid repeating the query, and continue with
        # dedicated tools or other verified results. Transport and other MCP
        # failures remain fatal.
        if tool_name == "run_select" and "Read-only query failed:" in str(exc):
            query_error = [{"error": str(exc)}]
            append_event(role, "mcp_tool_completed", tool_name, args, query_error, "query_error")
            return query_error
        raise
    append_event(role, "mcp_tool_completed", tool_name, args, output, "not_found" if output in (None, [], {}) else "tool_returned")
    return output


# Typed wrappers expose only role-appropriate MCP operations to each PydanticAI agent.
async def get_today() -> dict[str, Any]:
    """Read the shop operational date from desk.date_today."""
    return await call_mcp("get_today")


async def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Read one ticket and its prior agent board notes."""
    return await call_mcp("get_ticket", {"ticket_id": ticket_id})


async def list_open_tickets(limit: int = 50) -> list[dict[str, Any]]:
    """List currently open ticket records."""
    return await call_mcp("list_open_tickets", {"limit": limit})


async def list_pending_approvals() -> list[dict[str, Any]]:
    """Read payment and purchase requests awaiting human approval."""
    return await call_mcp("list_pending_approvals")


async def add_agent_note(ticket_id: int, agent: str, note: str) -> dict[str, Any]:
    """Append a specialist handoff or decision to the ticket board."""
    return await call_mcp("add_agent_note", {"ticket_id": ticket_id, "agent": agent, "note": note})


async def resolve_ticket(ticket_id: int, resolution: str, status: TicketStatus) -> dict[str, Any]:
    """Save the Boss final decision and status."""
    if status == "blocked" and "block" not in resolution.lower():
        # The MCP contract requires a clear blocker label. Preserve the Boss's
        # supplied, database-grounded reason and normalize only that label.
        append_event(
            "Boss",
            "blocked_resolution_labeled",
            "resolve_ticket",
            {"ticket_id": ticket_id, "status": status},
            {"normalization": "prefixed Blocker; resolution text preserved"},
            "tool_contract_format",
        )
        resolution = f"Blocker: {resolution}"
    saved = await call_mcp("resolve_ticket", {"ticket_id": ticket_id, "resolution": resolution, "status": status})
    run_state = TICKET_RUN_STATE.get()
    if run_state is not None:
        run_state["saved_ticket"] = saved
        run_state["resolved_by_agent"] = CURRENT_AGENT.get() == "Boss"
    return saved


async def check_stock(sku: str, size: str) -> dict[str, Any]:
    """Check quantity for the exact inventory SKU and size."""
    return await call_mcp("check_stock", {"sku": sku, "size": size})


async def find_vendors_for_sku(sku: str) -> dict[str, Any]:
    """Find category candidates from stored inventory names and vendor specialties."""
    return await call_mcp("find_vendors_for_sku", {"sku": sku})


async def vendor_status(vendor_id: int, sku: str | None = None) -> dict[str, Any]:
    """Read lead time and invoice blockers for one vendor."""
    return await call_mcp("vendor_status", {"vendor_id": vendor_id, "sku": sku})


async def get_cash() -> dict[str, Any]:
    """Read recorded cash balances and pending payment commitments."""
    return await call_mcp("get_cash")


async def get_open_invoices() -> list[dict[str, Any]]:
    """Read unpaid invoices with vendor names and database-date overdue flags."""
    return await call_mcp("get_open_invoices")


async def get_leases() -> list[dict[str, Any]]:
    """Read shop leases and due state relative to the recorded desk date."""
    return await call_mcp("get_leases")


async def run_select(sql: str) -> list[dict[str, Any]]:
    """Run a bounded, read-only SQL SELECT when no dedicated tool is available."""
    return await call_mcp("run_select", {"sql": sql})


async def request_payment(ticket_id: int, amount: float, reason: str, account_name: str = "checking", invoice_id: int | None = None, lease_id: int | None = None) -> dict[str, Any]:
    """Create a payment request for human approval; does not execute payment."""
    return await call_mcp("request_payment", {"ticket_id": ticket_id, "amount": amount, "reason": reason, "account_name": account_name, "invoice_id": invoice_id, "lease_id": lease_id})


async def create_purchase_order(ticket_id: int, vendor_id: int, sku: str, size: str, quantity: int, reason: str) -> dict[str, Any]:
    """Create a purchase-order request for human approval."""
    return await call_mcp("create_purchase_order", {"ticket_id": ticket_id, "vendor_id": vendor_id, "sku": sku, "size": size, "quantity": quantity, "reason": reason})


async def save_draft(ticket_id: int, subject: str, body: str) -> dict[str, Any]:
    """Save a customer message draft on the board without sending it."""
    return await call_mcp("save_draft", {"ticket_id": ticket_id, "subject": subject, "body": body})


async def describe_schema() -> list[dict[str, Any]]:
    """Describe the available database tables and fields before an unfamiliar query."""
    return await call_mcp("describe_schema")


TOOL_WRAPPERS = {
    "get_today": get_today,
    "get_ticket": get_ticket,
    "list_open_tickets": list_open_tickets,
    "list_pending_approvals": list_pending_approvals,
    "add_agent_note": add_agent_note,
    "resolve_ticket": resolve_ticket,
    "check_stock": check_stock,
    "find_vendors_for_sku": find_vendors_for_sku,
    "vendor_status": vendor_status,
    "get_cash": get_cash,
    "get_open_invoices": get_open_invoices,
    "get_leases": get_leases,
    "run_select": run_select,
    "request_payment": request_payment,
    "create_purchase_order": create_purchase_order,
    "save_draft": save_draft,
    "describe_schema": describe_schema,
}

ROLE_PROMPTS = {
    "Boss": "boss.md",
    "Inventory": "inventory.md",
    "Accounting": "accounting.md",
    "Facilities": "facilities.md",
    "Customer Service": "customer_service.md",
}


@lru_cache(maxsize=1)
def _model() -> OpenAIChatModel:
    api_key = os.getenv("PORTKEY_API_KEY")
    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY is missing from the course root .env file.")
    provider = OpenAIProvider(base_url="https://api.portkey.ai/v1", api_key=api_key)
    return OpenAIChatModel(MODEL_NAME, provider=provider)


def _instructions(role: str) -> str:
    shared = (PROMPT_ROOT / "common.md").read_text(encoding="utf-8")
    role_prompt = (PROMPT_ROOT / ROLE_PROMPTS[role]).read_text(encoding="utf-8")
    return f"{shared}\n\n{role_prompt}"


async def _delegate(role: str, ticket_id: int, question: str) -> dict[str, Any]:
    current_ticket = CURRENT_TICKET.get()
    if current_ticket is not None and ticket_id != current_ticket:
        reason = f"Agent is scoped to ticket {current_ticket}; refusing delegation for ticket {ticket_id}."
        append_event(CURRENT_AGENT.get(), "delegation_scope_refused", f"ask_{role.lower().replace(' ', '_')}", {"ticket_id": ticket_id}, reason, "ticket_scope_blocked")
        raise ValueError(reason)
    run_state = TICKET_RUN_STATE.get()
    if CURRENT_AGENT.get() == "Boss" and run_state and run_state.get("resolved_by_agent"):
        reason = "resolve_ticket has already been called; no further delegation is allowed."
        append_event(CURRENT_AGENT.get(), "post_resolution_delegation_refused", f"ask_{role.lower().replace(' ', '_')}", {"ticket_id": ticket_id}, reason, "resolution_was_final")
        raise ValueError(reason)
    state = DELEGATION_COUNT.get()
    if state is None:
        state = [0]
        DELEGATION_COUNT.set(state)
    if state[0] >= MAX_BOSS_DELEGATIONS:
        raise ValueError(f"Delegation limit ({MAX_BOSS_DELEGATIONS}) reached; report what remains unknown.")
    state[0] += 1
    requester = CURRENT_AGENT.get()
    append_event(requester, "delegation_started", f"ask_{role.lower().replace(' ', '_')}", {"ticket_id": ticket_id, "question": question}, "")
    try:
        specialist = build_agent(role)
        result = await _run_agent(role, specialist, f"Ticket ID: {ticket_id}\nSpecific question from {requester}: {question}", ticket_id)
        report = result.output
        append_event(requester, "delegation_completed", f"ask_{role.lower().replace(' ', '_')}", {"ticket_id": ticket_id}, report.model_dump(mode="json"))
        return report.model_dump(mode="json")
    except Exception as exc:
        append_event(requester, "delegation_error", f"ask_{role.lower().replace(' ', '_')}", {"ticket_id": ticket_id}, str(exc), "delegation_error")
        raise


def _make_accounting_delegate(requesting_agent: str):
    async def ask_accounting(ticket_id: int, question: str) -> dict[str, Any]:
        """Ask Accounting one focused question about cash, invoices, margin, or approvals."""
        return await _delegate("Accounting", ticket_id, question)
    ask_accounting.__name__ = f"ask_accounting_from_{requesting_agent.lower()}"
    return ask_accounting


def build_agent(role: str) -> Agent[Any, Any]:
    if role not in ROLE_PROMPTS:
        raise ValueError(f"Unknown agent role: {role}")
    output_type: type[Any] = SpecialistReport if role != "Boss" else TicketResolution
    agent: Agent[Any, Any] = Agent(
        _model(),
        name=role.lower().replace(" ", "_"),
        output_type=output_type,
        instructions=_instructions(role),
        # Portkey's gpt-6-luna chat-completions endpoint rejects non-none
        # reasoning_effort when function tools are present. Keep tool calling
        # compatible while retaining the requested Luna model.
        model_settings={"openai_reasoning_effort": "none"},
        retries=MAX_OUTPUT_RETRIES,
        tool_timeout=45,
    )
    shared = ["get_today", "get_ticket", "add_agent_note", "describe_schema", "run_select"]
    role_tools: dict[str, list[str]] = {
        # The dashboard supplies one ticket ID. The Boss therefore does not
        # need queue/search or specialist lookup tools; resolve_ticket records
        # the final decision, so a separate Boss note would be redundant.
        "Boss": ["get_today", "get_ticket", "list_pending_approvals", "resolve_ticket"],
        "Inventory": shared + ["check_stock", "find_vendors_for_sku", "vendor_status"],
        # Accounting's authorized lookups are already named in its prompt;
        # omit schema discovery to avoid a redundant full-schema response.
        "Accounting": ["get_today", "get_ticket", "add_agent_note", "run_select", "get_cash", "get_open_invoices", "request_payment", "create_purchase_order"],
        "Facilities": shared + ["get_leases"],
        "Customer Service": shared + ["check_stock", "save_draft"],
    }
    serialized_writes = {"add_agent_note", "resolve_ticket", "request_payment", "create_purchase_order", "save_draft"}
    for name in role_tools[role]:
        # A failed MCP operation is evidence, not a reason to repeat a write/read.
        # Output validation may retry once, but individual tools never retry.
        agent.tool_plain(
            TOOL_WRAPPERS[name],
            retries=0,
            sequential=name in serialized_writes,
        )

    if role == "Boss":
        async def ask_inventory(ticket_id: int, question: str) -> dict[str, Any]:
            """Delegate stock, shortfall, vendor lead-time, or restock blockers to Inventory."""
            return await _delegate("Inventory", ticket_id, question)

        async def ask_accounting(ticket_id: int, question: str) -> dict[str, Any]:
            """Delegate cash, invoices, margin, or payment approval analysis to Accounting."""
            return await _delegate("Accounting", ticket_id, question)

        async def ask_facilities(ticket_id: int, question: str) -> dict[str, Any]:
            """Delegate lease and rent analysis to Facilities."""
            return await _delegate("Facilities", ticket_id, question)

        async def ask_customer_service(ticket_id: int, question: str) -> dict[str, Any]:
            """Ask Customer Service to draft a message using confirmed ticket facts."""
            return await _delegate("Customer Service", ticket_id, question)

        agent.tool_plain(ask_inventory, retries=0, sequential=True)
        agent.tool_plain(ask_accounting, retries=0, sequential=True)
        agent.tool_plain(ask_facilities, retries=0, sequential=True)
        agent.tool_plain(ask_customer_service, retries=0, sequential=True)
    elif role in {"Inventory", "Facilities"}:
        agent.tool_plain(_make_accounting_delegate(role), name="ask_accounting", retries=0, sequential=True)
    return agent


async def _run_agent(role: str, agent: Agent[Any, Any], prompt: str, ticket_id: int | None) -> Any:
    agent_token = CURRENT_AGENT.set(role)
    ticket_token = CURRENT_TICKET.set(ticket_id)
    limits = UsageLimits(
        # Six model requests can be needed for a specialist's bounded path
        # (initial context, up to four tool steps, then the final typed report).
        request_limit=9 if role == "Boss" else 7,
        tool_calls_limit=MAX_TOOL_CALLS_PER_AGENT,
        # Inventory's exact stock/vendor workflow needs several bounded tool
        # round-trips; 14k was truncating valid runs before a typed summary.
        # Keep it capped at 20k (same as Accounting) rather than allowing an
        # unbounded loop.
        total_tokens_limit=24000 if role == "Boss" else (20000 if role in {"Accounting", "Inventory"} else 14000),
    )
    try:
        append_event(role, "agent_started", "pydantic_ai.iter", {"ticket_id": ticket_id, "model": MODEL_NAME}, "")
        # Drive PydanticAI one graph node at a time. This writes audit entries as
        # each model/tool node is reached, not only after the entire run finishes.
        async with agent.iter(prompt, usage_limits=limits) as run:
            node = run.next_node
            step = 0
            while type(node).__name__ != "End":
                step += 1
                tool_names: list[str] = []
                response = getattr(node, "model_response", None)
                parts = getattr(response, "parts", []) if response is not None else []
                for part in parts:
                    name = getattr(part, "tool_name", None)
                    if name:
                        tool_names.append(name)
                append_event(
                    role,
                    "model_loop_step",
                    "pydantic_ai.agent_loop",
                    {"ticket_id": ticket_id, "step": step, "node": type(node).__name__},
                    {"tool_names": tool_names},
                    "node_started",
                )
                node = await run.next(node)
            result = run.result
        if result is None:
            raise RuntimeError("PydanticAI agent loop ended without a result.")
        output = result.output
        append_event(role, "agent_completed", "pydantic_ai.iter", {"ticket_id": ticket_id}, output.model_dump(mode="json"), "completed")
        return result
    except Exception as exc:
        append_event(role, "agent_error", "pydantic_ai.iter", {"ticket_id": ticket_id}, f"{type(exc).__name__}: {exc}", "agent_error")
        raise
    finally:
        CURRENT_TICKET.reset(ticket_token)
        CURRENT_AGENT.reset(agent_token)


async def process_ticket(ticket_id: int) -> TicketRunResult:
    """Run the Boss workflow for one real ticket and ensure its returned decision is saved."""
    boss = build_agent("Boss")
    ticket_token = CURRENT_TICKET.set(ticket_id)
    role_token = CURRENT_AGENT.set("Orchestrator")
    run_state: dict[str, Any] = {}
    state_token = TICKET_RUN_STATE.set(run_state)
    delegation_token = DELEGATION_COUNT.set([0])
    try:
        # Read the authoritative date before processing so the returned run record
        # never needs a post-resolution database lookup.
        today = await call_mcp("get_today")
        result = await _run_agent(
            "Boss",
            boss,
            f"Process ticket {ticket_id} end to end. Follow the Boss workflow and save the final resolution with resolve_ticket.",
            ticket_id,
        )
        decision: TicketResolution = result.output
        saved = run_state.get("saved_ticket")
        if not saved or saved["status"] != decision.status:
            # Repair only through the terminal resolution tool, with no reads after it.
            saved = await resolve_ticket(ticket_id, decision.resolution, decision.status)
        append_event("Orchestrator", "ticket_workflow_completed", "process_ticket", {"ticket_id": ticket_id}, {"status": saved["status"], "category": decision.category}, decision.status)
        return TicketRunResult(ticket_id=ticket_id, date_today=today["date_today"], decision=decision, saved_ticket=saved)
    finally:
        DELEGATION_COUNT.reset(delegation_token)
        TICKET_RUN_STATE.reset(state_token)
        CURRENT_AGENT.reset(role_token)
        CURRENT_TICKET.reset(ticket_token)


async def process_open_tickets(limit: int = MAX_TICKETS_PER_BATCH) -> TeamRunResult:
    """Process a bounded batch of open tickets sequentially, avoiding runaway agent work."""
    if not 1 <= limit <= MAX_TICKETS_PER_BATCH:
        raise ValueError(f"limit must be between 1 and {MAX_TICKETS_PER_BATCH}.")
    open_tickets = await call_mcp("list_open_tickets", {"limit": limit})
    results = []
    for ticket in open_tickets[:limit]:
        results.append(await process_ticket(int(ticket["id"])))
    remaining = await call_mcp("list_open_tickets", {"limit": 50})
    return TeamRunResult(processed=len(results), results=results, remaining_open_ticket_ids=[int(item["id"]) for item in remaining])


async def main() -> None:
    """CLI entry point for bounded open-ticket processing."""
    result = await process_open_tickets()
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
