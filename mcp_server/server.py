"""Trusted FastMCP tools for Campus Customs operational tickets.

Run from the Homework 5 folder with:
    .venv/bin/fastmcp run mcp_server/server.py:mcp
"""
from __future__ import annotations

import sqlite3
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "campus_customs_new.db"
MAX_SEARCH_RESULTS = 50

mcp = FastMCP("Campus Customs Tickets")


def database_connection() -> sqlite3.Connection:
    """Open the approved Homework 5 database copy with foreign keys enabled."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    ensure_agent_tables(connection)
    return connection


def ensure_agent_tables(connection: sqlite3.Connection) -> None:
    """Initialize agent workflow tables in the working database copy only."""
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS agent_board_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            agent TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );
        CREATE TABLE IF NOT EXISTS agent_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL CHECK (action_type IN ('payment', 'purchase_order')),
            ticket_id INTEGER NOT NULL,
            reference_type TEXT,
            reference_id INTEGER,
            account_name TEXT,
            vendor_id INTEGER,
            sku TEXT,
            size TEXT,
            quantity INTEGER,
            amount REAL NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        );
        CREATE TABLE IF NOT EXISTS customer_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id)
        );
    """)
    action_columns = {row["name"] for row in connection.execute("PRAGMA table_info(agent_actions)").fetchall()}
    if "account_name" not in action_columns:
        connection.execute("ALTER TABLE agent_actions ADD COLUMN account_name TEXT")


def row_as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def require_ticket(connection: sqlite3.Connection, ticket_id: int) -> dict[str, Any]:
    ticket = row_as_dict(connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())
    if ticket is None:
        raise ValueError(f"Not Found: ticket {ticket_id} does not exist.")
    return ticket


def require_sku(connection: sqlite3.Connection, sku: str, size: str | None) -> None:
    """Only attach a new ticket to a SKU/size that exists in real inventory."""
    if size:
        row = connection.execute("SELECT 1 FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        label = f"SKU {sku} in size {size}"
    else:
        row = connection.execute("SELECT 1 FROM inventory WHERE sku = ?", (sku,)).fetchone()
        label = f"SKU {sku}"
    if row is None:
        raise ValueError(f"Not Found: {label} is not in inventory.")


@mcp.tool()
def search_tickets(query: str = "", status: str | None = None, ticket_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Search real ticket rows by text, status, and/or ticket type.

    Text matches the requester, subject, notes, SKU, or ticket ID. Results are capped
    to keep a client response bounded and come only from the tickets table.
    """
    if not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}.")
    clauses: list[str] = []
    values: list[Any] = []
    if query.strip():
        term = f"%{query.strip()}%"
        clauses.append("(CAST(id AS TEXT) LIKE ? OR requester LIKE ? OR subject LIKE ? OR notes LIKE ? OR sku LIKE ?)")
        values.extend([term] * 5)
    if status is not None:
        clauses.append("status = ?")
        values.append(status.strip())
    if ticket_type is not None:
        clauses.append("type = ?")
        values.append(ticket_type.strip())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with database_connection() as connection:
        rows = connection.execute(f"SELECT * FROM tickets{where} ORDER BY created_at DESC, id DESC LIMIT ?", [*values, limit]).fetchall()
    return [dict(row) for row in rows]


@mcp.tool()
def get_ticket(ticket_id: int) -> dict[str, Any]:
    """Get a real ticket and its eight most recent agent board notes."""
    with database_connection() as connection:
        ticket = require_ticket(connection, ticket_id)
        count = int(connection.execute(
            "SELECT COUNT(*) FROM agent_board_notes WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()[0])
        recent_notes = connection.execute(
            """SELECT agent, note, created_at FROM agent_board_notes
               WHERE ticket_id = ? ORDER BY id DESC LIMIT 8""",
            (ticket_id,),
        ).fetchall()
        ticket["board_notes"] = [dict(row) for row in reversed(recent_notes)]
        ticket["board_notes_omitted"] = max(0, count - len(recent_notes))
        ticket["agent_actions"] = [dict(row) for row in connection.execute(
            """SELECT id, action_type, vendor_id, sku, size, quantity, amount, status, created_at
               FROM agent_actions WHERE ticket_id = ? ORDER BY id""",
            (ticket_id,),
        ).fetchall()]
        return ticket


@mcp.tool()
def get_today() -> dict[str, Any]:
    """Return the shop's operational date from desk.date_today and its desk notes."""
    with database_connection() as connection:
        row = connection.execute("SELECT date_today, notes FROM desk LIMIT 1").fetchone()
    if row is None:
        raise ValueError("Not Found: desk date_today is not recorded.")
    return dict(row)


@mcp.tool()
def list_open_tickets(limit: int = 50) -> list[dict[str, Any]]:
    """List the bounded set of tickets whose stored status is exactly open."""
    if not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}.")
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM tickets WHERE lower(status) = 'open' ORDER BY created_at, id LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@mcp.tool()
def add_agent_note(ticket_id: int, agent: str, note: str) -> dict[str, Any]:
    """Append a specialist handoff or decision note to the ticket's board history."""
    if not agent.strip() or not note.strip():
        raise ValueError("agent and note are required.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        cursor = connection.execute(
            "INSERT INTO agent_board_notes (ticket_id, agent, note, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, agent.strip(), note.strip(), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM agent_board_notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


@mcp.tool()
def resolve_ticket(ticket_id: int, resolution: str, status: str) -> dict[str, Any]:
    """Record the Boss's final resolution and set pending_approval/resolved/blocked."""
    allowed = {"pending_approval", "resolved", "blocked"}
    if status not in allowed:
        raise ValueError(f"status must be one of {', '.join(sorted(allowed))}.")
    if not resolution.strip():
        raise ValueError("resolution must include facts and the remaining action or reason.")
    if status == "blocked" and "block" not in resolution.lower():
        raise ValueError("A blocked ticket needs an explicit blocker in resolution.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        pending = connection.execute(
            "SELECT COUNT(*) FROM agent_actions WHERE ticket_id = ? AND status = 'pending_approval'", (ticket_id,)
        ).fetchone()[0]
        if status == "resolved" and pending:
            raise ValueError("Not Found: pending human approvals remain; resolve as pending_approval.")
        connection.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        connection.execute(
            "INSERT INTO agent_board_notes (ticket_id, agent, note, created_at) VALUES (?, 'Boss', ?, ?)",
            (ticket_id, resolution.strip(), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        return require_ticket(connection, ticket_id)


@mcp.tool()
def check_stock(sku: str, size: str) -> dict[str, Any]:
    """Look up exact on-hand quantity for a SKU and size pair."""
    with database_connection() as connection:
        row = connection.execute("SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
    if row is None:
        raise ValueError(f"Not Found: inventory for SKU {sku}, size {size}.")
    return dict(row)


@mcp.tool()
def get_cash() -> dict[str, Any]:
    """Return recorded cash balances and pending payment/purchase commitments."""
    with database_connection() as connection:
        accounts = [dict(row) for row in connection.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name").fetchall()]
        pending = connection.execute(
            "SELECT COALESCE(account_name, 'checking') AS account_name, SUM(amount) AS amount FROM agent_actions WHERE status = 'pending_approval' GROUP BY COALESCE(account_name, 'checking')"
        ).fetchall()
    pending_by_account = {row["account_name"]: float(row["amount"] or 0) for row in pending}
    for account in accounts:
        account["pending_obligations"] = pending_by_account.get(account["name"], 0.0)
        account["available_before_new_request"] = max(0.0, float(account["balance"]) - account["pending_obligations"])
    return {"accounts": accounts}


@mcp.tool()
def get_open_invoices() -> list[dict[str, Any]]:
    """List unpaid invoices with vendor names and overdue state based on desk date."""
    with database_connection() as connection:
        today_row = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if today_row is None:
            raise ValueError("Not Found: desk date_today is not recorded.")
        today = date.fromisoformat(today_row["date_today"][:10])
        rows = connection.execute(
            """SELECT i.*, v.name AS vendor_name, v.specialty AS vendor_specialty
               FROM invoices i JOIN vendors v ON v.id = i.vendor_id
               WHERE lower(i.status) NOT IN ('paid', 'closed', 'void')
               ORDER BY i.due_date, i.id"""
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["overdue"] = date.fromisoformat(item["due_date"][:10]) < today
        results.append(item)
    return results


@mcp.tool()
def get_leases() -> list[dict[str, Any]]:
    """Return lease facts with due state computed from the recorded desk date."""
    with database_connection() as connection:
        today_row = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        if today_row is None:
            raise ValueError("Not Found: desk date_today is not recorded.")
        today = date.fromisoformat(today_row["date_today"][:10])
        rows = connection.execute("SELECT * FROM leases ORDER BY next_due, id").fetchall()
    results = []
    for row in rows:
        item = dict(row)
        due = date.fromisoformat(item["next_due"][:10])
        item["overdue"] = due < today
        item["due_today"] = due == today
        item["days_until_due"] = (due - today).days
        results.append(item)
    return results


@mcp.tool()
def vendor_status(vendor_id: int, sku: str | None = None) -> dict[str, Any]:
    """Return one vendor's lead time, invoice blockers, and expected arrival if SKU is known.

    The source schema has no SKU-to-vendor mapping. This tool reports that limitation
    instead of claiming a vendor supplies a particular SKU.
    """
    with database_connection() as connection:
        vendor = row_as_dict(connection.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone())
        if vendor is None:
            raise ValueError(f"Not Found: vendor {vendor_id}.")
        invoices = [dict(row) for row in connection.execute(
            "SELECT id, amount, due_date, status, description FROM invoices WHERE vendor_id = ? AND lower(status) NOT IN ('paid', 'closed', 'void') ORDER BY due_date, id",
            (vendor_id,),
        ).fetchall()]
        today_row = connection.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
        product = None
        if sku:
            product = row_as_dict(connection.execute("SELECT sku, name FROM inventory WHERE sku = ? LIMIT 1", (sku,)).fetchone())
            if product is None:
                raise ValueError(f"Not Found: SKU {sku} in inventory.")
    today = date.fromisoformat(today_row["date_today"][:10]) if today_row else None
    arrival = (today + timedelta(days=int(vendor["lead_days"]))).isoformat() if today else None
    return {
        "vendor": vendor,
        "open_invoices": invoices,
        "can_ship": not invoices,
        "expected_arrival": arrival,
        "sku": product,
        "sku_vendor_mapping": "not recorded in database" if sku else None,
    }


@mcp.tool()
def find_vendors_for_sku(sku: str) -> dict[str, Any]:
    """Find vendor candidates using inventory names and vendor specialty wording.

    The database has no vendor-SKU relation. Candidate matching uses only explicit
    category words shared between inventory item names and vendor specialties; it
    returns the matching basis so the agent can describe this limitation accurately.
    """
    with database_connection() as connection:
        products = [dict(row) for row in connection.execute("SELECT DISTINCT sku, name FROM inventory WHERE sku = ?", (sku,)).fetchall()]
        vendors = [dict(row) for row in connection.execute("SELECT id, name, specialty, lead_days FROM vendors ORDER BY lead_days, id").fetchall()]
    if not products:
        raise ValueError(f"Not Found: SKU {sku} in inventory.")
    product = products[0]
    product_text = f"{product['sku']} {product['name']}".lower()
    candidates = []
    for vendor in vendors:
        specialty = vendor["specialty"].lower()
        matched_category = None
        if any(term in product_text for term in ("hoodie", "tee", "shirt", "apparel", "crewneck", "sweater", "jacket")) and "apparel" in specialty:
            matched_category = "apparel"
        elif any(term in product_text for term in ("mug", "hat", "cap")) and any(term in specialty for term in ("mug", "small goods")):
            matched_category = "small goods"
        if matched_category:
            candidates.append({**vendor, "candidate_basis": f"product name/SKU category aligns with vendor specialty '{matched_category}'"})
    return {
        "sku": product["sku"],
        "product_name": product["name"],
        "candidates": candidates,
        "mapping_note": "No explicit vendor-to-SKU mapping exists; candidates are category matches based on stored inventory names and vendor specialties.",
    }


@mcp.tool()
def list_pending_approvals() -> list[dict[str, Any]]:
    """List payment and purchase-order requests waiting for a human decision."""
    with database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM agent_actions WHERE status = 'pending_approval' ORDER BY created_at, id"
        ).fetchall()
    return [dict(row) for row in rows]


@mcp.tool()
def request_payment(ticket_id: int, amount: float, reason: str, account_name: str = "checking", invoice_id: int | None = None, lease_id: int | None = None) -> dict[str, Any]:
    """Create a pending human approval for a payment; never moves cash."""
    if amount <= 0 or not reason.strip():
        raise ValueError("amount must be positive and reason is required.")
    if invoice_id is not None and lease_id is not None:
        raise ValueError("Choose one payment reference: invoice_id or lease_id.")
    if invoice_id is None and lease_id is None:
        raise ValueError("Specify an existing invoice_id or lease_id; unreferenced payment requests are not allowed.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        account = connection.execute("SELECT balance FROM cash_accounts WHERE name = ?", (account_name,)).fetchone()
        if account is None:
            raise ValueError(f"Not Found: cash account {account_name}.")
        if invoice_id is not None:
            invoice = connection.execute("SELECT amount, vendor_id, status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
            if invoice is None:
                raise ValueError(f"Not Found: invoice {invoice_id}.")
            if invoice["status"].lower() in {"paid", "closed", "void"}:
                raise ValueError(f"Invoice {invoice_id} is already {invoice['status']}.")
            if abs(float(invoice["amount"]) - amount) > 0.005:
                raise ValueError(f"Payment amount must match invoice {invoice_id} amount {float(invoice['amount']):.2f}.")
            duplicate = connection.execute(
                "SELECT id FROM agent_actions WHERE action_type = 'payment' AND reference_type = 'invoice' AND reference_id = ? AND status IN ('pending_approval', 'approved')",
                (invoice_id,),
            ).fetchone()
            paid = connection.execute("SELECT id FROM payments WHERE lower(kind) LIKE '%invoice%' AND ref_id = ? LIMIT 1", (invoice_id,)).fetchone()
            if duplicate or paid:
                raise ValueError(f"Invoice {invoice_id} already has a pending/approved request or recorded payment.")
        elif lease_id is not None:
            lease = connection.execute("SELECT monthly_rent FROM leases WHERE id = ?", (lease_id,)).fetchone()
            if lease is None:
                raise ValueError(f"Not Found: lease {lease_id}.")
            if abs(float(lease["monthly_rent"]) - amount) > 0.005:
                raise ValueError(f"Payment amount must match lease {lease_id} monthly_rent {float(lease['monthly_rent']):.2f}.")
            duplicate = connection.execute(
                "SELECT id FROM agent_actions WHERE action_type = 'payment' AND reference_type = 'lease' AND reference_id = ? AND status IN ('pending_approval', 'approved')",
                (lease_id,),
            ).fetchone()
            paid = connection.execute("SELECT id FROM payments WHERE (lower(kind) LIKE '%lease%' OR lower(kind) LIKE '%rent%') AND ref_id = ? LIMIT 1", (lease_id,)).fetchone()
            if duplicate or paid:
                raise ValueError(f"Lease {lease_id} already has a pending/approved request or recorded payment.")
        commitments = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM agent_actions WHERE COALESCE(account_name, 'checking') = ? AND status = 'pending_approval'",
            (account_name,),
        ).fetchone()[0]
        available = float(account["balance"]) - float(commitments or 0)
        if amount > available:
            raise ValueError(f"Cash shortfall: {account_name} has {available:.2f} available, request is {amount:.2f}.")
        cursor = connection.execute(
            """INSERT INTO agent_actions (action_type, ticket_id, reference_type, reference_id, account_name, amount, reason, status, created_at)
               VALUES ('payment', ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
            (ticket_id, "invoice" if invoice_id is not None else "lease", invoice_id if invoice_id is not None else lease_id, account_name, amount, reason.strip(), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        result = row_as_dict(connection.execute("SELECT * FROM agent_actions WHERE id = ?", (cursor.lastrowid,)).fetchone())
    return result or {}


@mcp.tool()
def create_purchase_order(ticket_id: int, vendor_id: int, sku: str, size: str, quantity: int, reason: str) -> dict[str, Any]:
    """Create a pending purchase-order approval after stock/vendor/cash checks."""
    if quantity < 1 or not reason.strip():
        raise ValueError("quantity must be positive and reason is required.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        stock = connection.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        if stock is None:
            raise ValueError(f"Not Found: inventory for SKU {sku}, size {size}.")
        vendor = connection.execute("SELECT lead_days FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            raise ValueError(f"Not Found: vendor {vendor_id}.")
        product = connection.execute("SELECT name FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        specialty = connection.execute("SELECT specialty FROM vendors WHERE id = ?", (vendor_id,)).fetchone()["specialty"].lower()
        product_name = product["name"].lower()
        apparel_candidate = any(term in product_name for term in ("hoodie", "tee", "shirt", "apparel", "crewneck", "sweater", "jacket")) and "apparel" in specialty
        goods_candidate = any(term in product_name for term in ("mug", "hat", "cap")) and any(term in specialty for term in ("mug", "small goods"))
        if not (apparel_candidate or goods_candidate):
            raise ValueError("No database-backed vendor category match for this SKU; the vendor-SKU mapping is not recorded.")
        blocker = connection.execute(
            "SELECT id FROM invoices WHERE vendor_id = ? AND lower(status) NOT IN ('paid', 'closed', 'void') LIMIT 1", (vendor_id,)
        ).fetchone()
        if blocker:
            raise ValueError(f"Vendor {vendor_id} is blocked by open invoice {blocker['id']}.")
        pricing = connection.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        if pricing is None:
            raise ValueError(f"Not Found: pricing for SKU {sku}.")
        account = connection.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()
        if account is None:
            raise ValueError("Not Found: checking cash account.")
        pending_cash = connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM agent_actions WHERE status = 'pending_approval'"
        ).fetchone()[0]
        amount = float(pricing["unit_cost"]) * quantity
        available = float(account["balance"]) - float(pending_cash or 0)
        if amount > available:
            raise ValueError(f"Cash shortfall: {available:.2f} available, purchase request is {amount:.2f}.")
        duplicate = connection.execute(
            "SELECT id FROM agent_actions WHERE action_type = 'purchase_order' AND ticket_id = ? AND vendor_id = ? AND sku = ? AND size = ? AND status IN ('pending_approval', 'approved')",
            (ticket_id, vendor_id, sku, size),
        ).fetchone()
        if duplicate:
            raise ValueError(f"Purchase order request {duplicate['id']} is already pending or approved.")
        cursor = connection.execute(
            """INSERT INTO agent_actions (action_type, ticket_id, vendor_id, sku, size, quantity, amount, reason, status, created_at)
               VALUES ('purchase_order', ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?)""",
            (ticket_id, vendor_id, sku, size, quantity, amount, reason.strip(), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        result = row_as_dict(connection.execute("SELECT * FROM agent_actions WHERE id = ?", (cursor.lastrowid,)).fetchone())
    return result or {}


@mcp.tool()
def save_draft(ticket_id: int, subject: str, body: str) -> dict[str, Any]:
    """Save a customer-service draft on the board. This never sends a message."""
    if not subject.strip() or not body.strip():
        raise ValueError("subject and body are required.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        cursor = connection.execute(
            "INSERT INTO customer_drafts (ticket_id, subject, body, status, created_at) VALUES (?, ?, ?, 'draft', ?)",
            (ticket_id, subject.strip(), body.strip(), datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
        result = row_as_dict(connection.execute("SELECT * FROM customer_drafts WHERE id = ?", (cursor.lastrowid,)).fetchone())
    return result or {}


@mcp.tool()
def describe_schema() -> list[dict[str, Any]]:
    """Describe the real tables and columns available in the working database copy."""
    with database_connection() as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [{"table": row["name"], "columns": [dict(column) for column in connection.execute(f"PRAGMA table_info({row['name']})").fetchall()]} for row in tables]


@mcp.tool()
def run_select(sql: str) -> list[dict[str, Any]]:
    """Run one read-only SELECT against the operational database; results are capped at 200 rows."""
    statement = sql.strip().rstrip(";").strip()
    if not re.match(r"(?is)^select\b", statement) or ";" in statement:
        raise ValueError("Only one read-only SELECT statement is allowed.")
    with database_connection() as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.set_progress_handler(lambda: 1, 100_000)
        try:
            cursor = connection.execute(statement)
            rows = cursor.fetchmany(201)
        except sqlite3.Error as exc:
            raise ValueError(f"Read-only query failed: {exc}") from exc
    if len(rows) > 200:
        rows = rows[:200]
    return [dict(row) for row in rows]


@mcp.tool()
def create_ticket(
    ticket_type: str,
    requester: str,
    subject: str,
    status: str,
    sku: str | None = None,
    size: str | None = None,
    qty: int | None = None,
    lease_id: int | None = None,
    invoice_id: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create a ticket using supplied operational facts and validated linked records.

    SKU/size references must exist in inventory; lease and invoice IDs must exist in
    their respective tables. The server supplies only the creation timestamp.
    """
    required = {"ticket_type": ticket_type, "requester": requester, "subject": subject, "status": status}
    blank = [name for name, value in required.items() if not value or not value.strip()]
    if blank:
        raise ValueError(f"Required values cannot be blank: {', '.join(blank)}.")
    if qty is not None and qty < 1:
        raise ValueError("qty must be at least 1 when supplied.")
    if size is not None and sku is None:
        raise ValueError("size requires a sku.")
    with database_connection() as connection:
        if sku is not None:
            require_sku(connection, sku, size)
        if lease_id is not None and connection.execute("SELECT 1 FROM leases WHERE id = ?", (lease_id,)).fetchone() is None:
            raise ValueError(f"Not Found: lease {lease_id} does not exist.")
        if invoice_id is not None and connection.execute("SELECT 1 FROM invoices WHERE id = ?", (invoice_id,)).fetchone() is None:
            raise ValueError(f"Not Found: invoice {invoice_id} does not exist.")
        cursor = connection.execute(
            """
            INSERT INTO tickets (type, requester, subject, sku, size, qty, lease_id, invoice_id, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticket_type.strip(), requester.strip(), subject.strip(), sku, size, qty, lease_id, invoice_id, status.strip(), notes.strip() if notes else None, datetime.now(timezone.utc).isoformat()),
        )
        ticket_id = int(cursor.lastrowid)
        connection.commit()
        return require_ticket(connection, ticket_id)


@mcp.tool()
def update_ticket_status(ticket_id: int, status: str) -> dict[str, Any]:
    """Update the status of an existing ticket and return the saved real row."""
    if not status or not status.strip():
        raise ValueError("status cannot be blank.")
    with database_connection() as connection:
        require_ticket(connection, ticket_id)
        connection.execute("UPDATE tickets SET status = ? WHERE id = ?", (status.strip(), ticket_id))
        connection.commit()
        return require_ticket(connection, ticket_id)


@mcp.tool()
def add_ticket_note(ticket_id: int, note: str) -> dict[str, Any]:
    """Append a non-empty note to an existing ticket without replacing prior notes."""
    if not note or not note.strip():
        raise ValueError("note cannot be blank.")
    with database_connection() as connection:
        ticket = require_ticket(connection, ticket_id)
        prior_notes = ticket["notes"]
        updated_notes = f"{prior_notes}\n{note.strip()}" if prior_notes else note.strip()
        connection.execute("UPDATE tickets SET notes = ? WHERE id = ?", (updated_notes, ticket_id))
        connection.commit()
        return require_ticket(connection, ticket_id)


if __name__ == "__main__":
    mcp.run()
