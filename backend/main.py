"""FastAPI dashboard API for the Campus Customs ticket team.

Run from this folder with:
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.audit import AUDIT_PATH, append_event  # noqa: E402
from backend.models import (  # noqa: E402
    AgentEventRecord,
    AgentEventsResponse,
    ApprovalExecutionResult,
    ApprovalRequest,
    CheckingBalanceResponse,
    DashboardApprovalRecord,
    DashboardTicket,
    DashboardTicketsResponse,
    PendingApprovalRecord,
    PendingApprovalsResponse,
    RejectionRequest,
    RejectionResult,
    ResetDatabaseRequest,
    ResetDatabaseResult,
    ShopTodayRecord,
    TicketRunResult,
)
from mcp_server.server import ensure_agent_tables  # noqa: E402


DATABASE_PATH = PROJECT_ROOT / "data" / "campus_customs_new.db"
ORIGINAL_DATABASE_PATH = PROJECT_ROOT / "data" / "campus_customs.db"
DASHBOARD_TICKET_IDS = (101, 102, 103)
AGENT_RUN_TIMEOUT_SECONDS = 300
_STATE_LOCK = asyncio.Lock()

app = FastAPI(
    title="Campus Customs Dashboard API",
    version="1.0.0",
    description="Ticket operations, agent events, human approvals, cash, and reset controls.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):51[7-9][0-9]+",
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    """Open the working copy, initialize workflow tables, and close reliably."""
    if not DATABASE_PATH.is_file():
        raise FileNotFoundError(f"Working database not found: {DATABASE_PATH}")
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        ensure_agent_tables(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _dashboard_ticket(row: sqlite3.Row) -> DashboardTicket:
    data = dict(row)
    status = str(data["status"]).strip().lower()
    data["is_open"] = status == "open"
    data["is_resolved"] = status == "resolved"
    return DashboardTicket.model_validate(data)


@app.get("/api/tickets", response_model=DashboardTicketsResponse, tags=["tickets"])
async def get_dashboard_tickets() -> DashboardTicketsResponse:
    """Return only the three operational tickets with their current status flags."""
    try:
        with database_connection() as connection:
            placeholders = ",".join("?" for _ in DASHBOARD_TICKET_IDS)
            rows = connection.execute(
                f"SELECT * FROM tickets WHERE id IN ({placeholders}) ORDER BY id",
                DASHBOARD_TICKET_IDS,
            ).fetchall()
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to read the working ticket database.") from exc
    found_ids = {int(row["id"]) for row in rows}
    missing_ids = sorted(set(DASHBOARD_TICKET_IDS) - found_ids)
    if missing_ids:
        raise HTTPException(status_code=500, detail=f"The working database is missing dashboard ticket(s): {missing_ids}.")
    return DashboardTicketsResponse(tickets=[_dashboard_ticket(row) for row in rows])


@app.get("/api/today", response_model=ShopTodayRecord, tags=["cash"])
async def get_shop_today() -> ShopTodayRecord:
    """Return the shop's authoritative operational date from desk.date_today."""
    try:
        with database_connection() as connection:
            row = connection.execute("SELECT date_today, notes FROM desk LIMIT 1").fetchone()
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to read the shop date.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="The desk date is not recorded.")
    return ShopTodayRecord.model_validate(dict(row))


@app.post("/api/tickets/{ticket_id}/run", response_model=TicketRunResult, tags=["tickets"])
async def run_ticket_agents(ticket_id: int) -> TicketRunResult:
    """Run the existing bounded PydanticAI team for one open dashboard ticket."""
    if ticket_id not in DASHBOARD_TICKET_IDS:
        raise HTTPException(status_code=404, detail="Only dashboard tickets 101, 102, and 103 can be run here.")
    async with _STATE_LOCK:
        try:
            with database_connection() as connection:
                row = connection.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        except (sqlite3.Error, FileNotFoundError) as exc:
            raise HTTPException(status_code=500, detail="Unable to read the working ticket database.") from exc
        if row is None:
            raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} was not found.")
        if str(row["status"]).lower() != "open":
            raise HTTPException(status_code=409, detail=f"Ticket {ticket_id} is not open and will not be run again.")

        append_event("Orchestrator", "dashboard_agent_run_requested", "POST /api/tickets/{ticket_id}/run", {"ticket_id": ticket_id}, "")
        try:
            from backend.agents import process_ticket

            return await asyncio.wait_for(process_ticket(ticket_id), timeout=AGENT_RUN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            append_event("Orchestrator", "dashboard_agent_run_timeout", "process_ticket", {"ticket_id": ticket_id}, "Agent run exceeded the API limit.", "timeout")
            raise HTTPException(status_code=504, detail="Agent run timed out; inspect the audit trail before retrying.") from exc
        except Exception as exc:
            append_event("Orchestrator", "dashboard_agent_run_error", "process_ticket", {"ticket_id": ticket_id}, f"{type(exc).__name__}: {exc}", "agent_run_error")
            raise HTTPException(status_code=502, detail="The agent run failed; inspect the audit trail for details.") from exc


def _read_audit_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            events = json.load(stream)
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="The audit trail could not be read as valid JSON.") from exc
    if not isinstance(events, list):
        raise HTTPException(status_code=500, detail="The audit trail must contain a JSON array.")
    return events


def _requester_agent_by_action() -> dict[int, str]:
    """Use the recorded MCP completion event to label who requested each approval."""
    request_tools = {"request_payment", "create_purchase_order"}
    requesters: dict[int, str] = {}
    for event in _read_audit_events():
        if event.get("tool_name") not in request_tools:
            continue
        try:
            result = json.loads(event.get("short_result", ""))
            action_id = int(result["id"])
            agent = str(event["agent"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if agent in {"Boss", "Inventory", "Accounting", "Facilities", "Customer Service"}:
            requesters[action_id] = agent
    return requesters


@app.get("/api/events", response_model=AgentEventsResponse, tags=["events"])
async def get_recent_agent_events(
    after: int | None = Query(default=None, ge=0, description="Return events after this event ID for incremental refresh."),
    limit: int = Query(default=100, ge=1, le=500),
) -> AgentEventsResponse:
    """Return recent append-only audit events, including agent outputs and tool calls."""
    events = _read_audit_events()
    total = len(events)
    start = max(0, total - limit) if after is None else min(after, total)
    end = min(start + limit, total)
    page: list[AgentEventRecord] = []
    for index in range(start, end):
        try:
            page.append(AgentEventRecord(id=index + 1, **events[index]))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Audit event {index + 1} has an invalid shape.") from exc
    return AgentEventsResponse(events=page, next_cursor=end, total=total)


@app.get("/api/approvals", response_model=PendingApprovalsResponse, tags=["approvals"])
async def get_pending_approvals() -> PendingApprovalsResponse:
    """List pending human decisions so a dashboard can show explicit approval controls."""
    try:
        with database_connection() as connection:
            rows = connection.execute(
                """SELECT aa.*, v_order.name AS order_vendor_name,
                          COALESCE(v_order.name, v_invoice.name, l.landlord) AS payee
                   FROM agent_actions aa
                   LEFT JOIN vendors v_order ON v_order.id = aa.vendor_id
                   LEFT JOIN invoices i ON aa.action_type = 'payment' AND aa.reference_type = 'invoice' AND i.id = aa.reference_id
                   LEFT JOIN vendors v_invoice ON v_invoice.id = i.vendor_id
                   LEFT JOIN leases l ON aa.action_type = 'payment' AND aa.reference_type = 'lease' AND l.id = aa.reference_id
                   WHERE aa.status = 'pending_approval' ORDER BY aa.created_at, aa.id LIMIT 100"""
            ).fetchall()
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to read pending approvals.") from exc
    requesters = _requester_agent_by_action()
    approvals = []
    for row in rows:
        data = dict(row)
        vendor_name = data.pop("order_vendor_name", None)
        data["vendor_name"] = vendor_name
        data["requested_by"] = requesters.get(int(data["id"]))
        approvals.append(DashboardApprovalRecord.model_validate(data))
    return PendingApprovalsResponse(approvals=approvals)


def _approve_action(action_id: int, approved_by: str) -> ApprovalExecutionResult:
    approver = approved_by.strip()
    if not approver:
        raise HTTPException(status_code=422, detail="approved_by must identify the human approver.")
    try:
        with database_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action_row = connection.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
            if action_row is None:
                raise HTTPException(status_code=404, detail=f"Approval {action_id} was not found.")
            action = dict(action_row)
            if action["status"] != "pending_approval":
                raise HTTPException(status_code=409, detail=f"Approval {action_id} is already {action['status']}.")

            account_name = action.get("account_name") or "checking"
            amount = float(action["amount"])
            if amount <= 0:
                raise HTTPException(status_code=409, detail="The stored approval amount must be positive.")
            account = connection.execute("SELECT balance FROM cash_accounts WHERE name = ?", (account_name,)).fetchone()
            if account is None:
                raise HTTPException(status_code=409, detail=f"Cash account {account_name!r} is not recorded.")
            other_pending = connection.execute(
                """SELECT COALESCE(SUM(amount), 0) FROM agent_actions
                   WHERE COALESCE(account_name, 'checking') = ? AND status = 'pending_approval' AND id <> ?""",
                (account_name, action_id),
            ).fetchone()[0]
            available = float(account["balance"]) - float(other_pending or 0)
            if amount > available + 0.000001:
                raise HTTPException(status_code=409, detail=f"Cash shortfall: {available:.2f} is available after other pending requests; approval is {amount:.2f}.")

            kind: str
            reference_id: int | None
            if action["action_type"] == "payment":
                reference_type = action.get("reference_type")
                reference_id = action.get("reference_id")
                if reference_id is None or reference_type not in {"invoice", "lease"}:
                    raise HTTPException(status_code=409, detail="The payment has no valid invoice or lease reference.")
                if reference_type == "invoice":
                    obligation = connection.execute("SELECT amount, status FROM invoices WHERE id = ?", (reference_id,)).fetchone()
                    if obligation is None:
                        raise HTTPException(status_code=409, detail=f"Invoice {reference_id} is not recorded.")
                    if str(obligation["status"]).lower() in {"paid", "closed", "void"}:
                        raise HTTPException(status_code=409, detail=f"Invoice {reference_id} is already {obligation['status']}.")
                    if abs(float(obligation["amount"]) - amount) > 0.005:
                        raise HTTPException(status_code=409, detail="The approval amount no longer matches the invoice.")
                    kind = "invoice"
                else:
                    obligation = connection.execute("SELECT monthly_rent FROM leases WHERE id = ?", (reference_id,)).fetchone()
                    if obligation is None:
                        raise HTTPException(status_code=409, detail=f"Lease {reference_id} is not recorded.")
                    if abs(float(obligation["monthly_rent"]) - amount) > 0.005:
                        raise HTTPException(status_code=409, detail="The approval amount no longer matches the monthly rent.")
                    kind = "lease"
                duplicate_payment = connection.execute(
                    "SELECT id FROM payments WHERE lower(kind) = ? AND ref_id = ? LIMIT 1",
                    (kind, reference_id),
                ).fetchone()
                if duplicate_payment:
                    raise HTTPException(status_code=409, detail=f"A payment is already recorded for {kind} {reference_id}.")
            elif action["action_type"] == "purchase_order":
                sku, size = action.get("sku"), action.get("size")
                quantity, vendor_id = action.get("quantity"), action.get("vendor_id")
                if not sku or not size or not quantity or not vendor_id:
                    raise HTTPException(status_code=409, detail="The stored purchase request is missing SKU, size, quantity, or vendor.")
                product = connection.execute("SELECT name FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
                vendor = connection.execute("SELECT specialty FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
                if product is None or vendor is None:
                    raise HTTPException(status_code=409, detail="The purchase SKU/size or vendor is no longer recorded.")
                product_name = str(product["name"]).lower()
                specialty = str(vendor["specialty"]).lower()
                apparel_candidate = any(term in product_name for term in ("hoodie", "tee", "shirt", "apparel", "crewneck", "sweater", "jacket")) and "apparel" in specialty
                goods_candidate = any(term in product_name for term in ("mug", "hat", "cap")) and any(term in specialty for term in ("mug", "small goods"))
                if not (apparel_candidate or goods_candidate):
                    raise HTTPException(status_code=409, detail="The stored vendor does not match a database-backed category for this product.")
                blocker = connection.execute(
                    "SELECT id FROM invoices WHERE vendor_id = ? AND lower(status) NOT IN ('paid', 'closed', 'void') LIMIT 1",
                    (vendor_id,),
                ).fetchone()
                if blocker:
                    raise HTTPException(status_code=409, detail=f"Vendor {vendor_id} is blocked by open invoice {blocker['id']}.")
                pricing = connection.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
                if pricing is None or abs(float(pricing["unit_cost"]) * int(quantity) - amount) > 0.005:
                    raise HTTPException(status_code=409, detail="The purchase amount no longer matches recorded unit cost times quantity.")
                kind = "purchase_order"
                reference_id = action_id
                if connection.execute("SELECT id FROM payments WHERE lower(kind) = 'purchase_order' AND ref_id = ? LIMIT 1", (action_id,)).fetchone():
                    raise HTTPException(status_code=409, detail=f"Purchase approval {action_id} already has a payment record.")
            else:
                raise HTTPException(status_code=409, detail=f"Unsupported approval type: {action['action_type']}.")

            paid_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            payment_cursor = connection.execute(
                "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, reference_id, amount, account_name, paid_at, approver),
            )
            updated = connection.execute(
                "UPDATE cash_accounts SET balance = balance - ? WHERE name = ? AND balance >= ?",
                (amount, account_name, amount),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Cash changed during approval; refresh the balance and review again.")
            if action["action_type"] == "payment" and action.get("reference_type") == "invoice":
                connection.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (action["reference_id"],))
            connection.execute("UPDATE agent_actions SET status = 'approved' WHERE id = ? AND status = 'pending_approval'", (action_id,))
            saved_action = dict(connection.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone())
            new_balance = float(connection.execute("SELECT balance FROM cash_accounts WHERE name = ?", (account_name,)).fetchone()["balance"])
            result = ApprovalExecutionResult(
                action=PendingApprovalRecord.model_validate(saved_action),
                payment_id=int(payment_cursor.lastrowid),
                account_name=account_name,
                new_balance=new_balance,
            )
        return result
    except HTTPException:
        raise
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to execute the approval transaction.") from exc


@app.post("/api/approvals/{action_id}/approve", response_model=ApprovalExecutionResult, tags=["approvals"])
async def approve_pending_action(action_id: int, request: ApprovalRequest) -> ApprovalExecutionResult:
    """Execute one pending payment or purchase only after explicit human confirmation."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true only after the human explicitly clicks approve.")
    async with _STATE_LOCK:
        result = _approve_action(action_id, request.approved_by)
        append_event(
            "Orchestrator",
            "human_approval_executed",
            "POST /api/approvals/{action_id}/approve",
            {"action_id": action_id, "approved_by": request.approved_by},
            {"action_type": result.action.action_type, "payment_id": result.payment_id, "amount": result.action.amount, "new_balance": result.new_balance},
            "approved",
        )
        return result


def _reject_action(action_id: int, rejected_by: str, reason: str | None) -> RejectionResult:
    rejector = rejected_by.strip()
    if not rejector:
        raise HTTPException(status_code=422, detail="rejected_by must identify the human decision-maker.")
    cleaned_reason = reason.strip() if reason and reason.strip() else None
    try:
        with database_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action = connection.execute("SELECT * FROM agent_actions WHERE id = ?", (action_id,)).fetchone()
            if action is None:
                raise HTTPException(status_code=404, detail=f"Approval {action_id} was not found.")
            if action["status"] != "pending_approval":
                raise HTTPException(status_code=409, detail=f"Approval {action_id} is already {action['status']}.")
            connection.execute("UPDATE agent_actions SET status = 'rejected' WHERE id = ?", (action_id,))
            pending = connection.execute(
                "SELECT COUNT(*) FROM agent_actions WHERE ticket_id = ? AND status = 'pending_approval'",
                (action["ticket_id"],),
            ).fetchone()[0]
            ticket = connection.execute("SELECT status FROM tickets WHERE id = ?", (action["ticket_id"],)).fetchone()
            ticket_status = str(ticket["status"]) if ticket else "Not Found"
            if ticket and not pending and ticket_status == "pending_approval":
                ticket_status = "blocked"
                connection.execute("UPDATE tickets SET status = 'blocked' WHERE id = ?", (action["ticket_id"],))
            note = f"Human approver {rejector} rejected approval {action_id}."
            if cleaned_reason:
                note += f" Reason: {cleaned_reason}"
            connection.execute(
                "INSERT INTO agent_board_notes (ticket_id, agent, note, created_at) VALUES (?, ?, ?, ?)",
                (action["ticket_id"], "Human approver", note, datetime.now(timezone.utc).isoformat()),
            )
        return RejectionResult(action_id=action_id, status="rejected", ticket_status=ticket_status)
    except HTTPException:
        raise
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to record the rejection.") from exc


@app.post("/api/approvals/{action_id}/reject", response_model=RejectionResult, tags=["approvals"])
async def reject_pending_action(action_id: int, request: RejectionRequest) -> RejectionResult:
    """Record a human rejection without moving cash; unresolved tickets become blocked."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true only after the human explicitly clicks reject.")
    async with _STATE_LOCK:
        result = _reject_action(action_id, request.rejected_by, request.reason)
        append_event(
            "Orchestrator",
            "human_approval_rejected",
            "POST /api/approvals/{action_id}/reject",
            {"action_id": action_id, "rejected_by": request.rejected_by, "reason": request.reason},
            {"ticket_status": result.ticket_status},
            "rejected",
        )
        return result


@app.get("/api/cash", response_model=CheckingBalanceResponse, tags=["cash"])
async def get_checking_balance() -> CheckingBalanceResponse:
    """Return the current checking balance and outstanding human-approved commitments."""
    try:
        with database_connection() as connection:
            account = connection.execute("SELECT name, balance, date FROM cash_accounts WHERE name = 'checking'").fetchone()
            if account is None:
                raise HTTPException(status_code=404, detail="Checking account is not recorded.")
            pending = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM agent_actions WHERE COALESCE(account_name, 'checking') = 'checking' AND status = 'pending_approval'"
            ).fetchone()[0]
    except HTTPException:
        raise
    except (sqlite3.Error, FileNotFoundError) as exc:
        raise HTTPException(status_code=500, detail="Unable to read the checking account.") from exc
    balance = float(account["balance"])
    pending_amount = float(pending or 0)
    return CheckingBalanceResponse(
        account_name=str(account["name"]),
        balance=balance,
        date=str(account["date"]),
        pending_obligations=pending_amount,
        available_balance=max(0.0, balance - pending_amount),
    )


@app.post("/api/reset", response_model=ResetDatabaseResult, tags=["maintenance"])
async def reset_working_database(request: ResetDatabaseRequest) -> ResetDatabaseResult:
    """Restore the working DB from the preserved original; never clear the audit trail."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to restore the working database from the original copy.")
    if not ORIGINAL_DATABASE_PATH.is_file():
        raise HTTPException(status_code=500, detail="The preserved original database is not available.")
    async with _STATE_LOCK:
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{DATABASE_PATH.name}.", suffix=".reset.tmp", dir=DATABASE_PATH.parent
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            shutil.copyfile(ORIGINAL_DATABASE_PATH, temporary_path)
            os.replace(temporary_path, DATABASE_PATH)
            temporary_path = None
            with database_connection() as connection:
                ticket_rows = connection.execute(
                    "SELECT id FROM tickets WHERE id IN (?, ?, ?) ORDER BY id", DASHBOARD_TICKET_IDS
                ).fetchall()
            restored_ids = [int(row["id"]) for row in ticket_rows]
            if tuple(restored_ids) != DASHBOARD_TICKET_IDS:
                raise HTTPException(status_code=500, detail="The original database does not contain all three dashboard tickets.")
        except HTTPException:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(status_code=500, detail="Unable to restore the working database.") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        append_event(
            "Orchestrator",
            "working_database_reset",
            "POST /api/reset",
            {"working_database": "data/campus_customs_new.db"},
            {"restored_ticket_ids": restored_ids, "audit_trail_preserved": True},
            "confirmed_reset",
        )
        return ResetDatabaseResult(
            restored_from="data/campus_customs.db",
            working_database="data/campus_customs_new.db",
            restored_ticket_ids=restored_ids,
            audit_trail_preserved=True,
        )
