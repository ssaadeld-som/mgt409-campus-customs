"""Typed data contracts for the Campus Customs agent team."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


TicketCategory = Literal["order_stock", "bill_invoice", "rent_lease", "discount_pricing", "customer_communication", "mixed"]
TicketStatus = Literal["pending_approval", "resolved", "blocked"]
AgentRole = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]
AuditActor = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service", "Orchestrator"]


class TrustedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditTrailEvent(TrustedModel):
    time: datetime
    agent: AuditActor
    step: str
    tool_name: str = ""
    short_arguments: str
    short_result: str
    stop_reason: str = ""


class TodayRecord(TrustedModel):
    date_today: date
    notes: str | None = None


class TicketRecord(TrustedModel):
    id: int
    type: str
    requester: str
    subject: str
    sku: str | None = None
    size: str | None = None
    qty: int | None = None
    lease_id: int | None = None
    invoice_id: int | None = None
    status: str
    notes: str | None = None
    created_at: str
    board_notes: list[dict[str, str]] = Field(default_factory=list)


class StockRecord(TrustedModel):
    sku: str
    name: str
    size: str
    qty: int = Field(ge=0)
    location: str


class VendorRecord(TrustedModel):
    id: int
    name: str
    specialty: str
    lead_days: int = Field(ge=0)


class VendorStatusRecord(TrustedModel):
    vendor: VendorRecord
    open_invoices: list[dict[str, object]] = Field(default_factory=list)
    can_ship: bool
    expected_arrival: date | None = None
    sku: dict[str, str] | None = None
    sku_vendor_mapping: str | None = None


class InvoiceRecord(TrustedModel):
    id: int
    vendor_id: int
    amount: float = Field(ge=0)
    due_date: date
    status: str
    description: str | None = None
    vendor_name: str | None = None
    overdue: bool


class LeaseRecord(TrustedModel):
    id: int
    space_name: str
    landlord: str
    monthly_rent: float = Field(ge=0)
    next_due: date
    notes: str | None = None
    overdue: bool
    due_today: bool
    days_until_due: int


class CashAccountRecord(TrustedModel):
    name: str
    balance: float
    date: date
    pending_obligations: float = 0
    available_before_new_request: float


class PendingApprovalRecord(TrustedModel):
    id: int
    action_type: Literal["payment", "purchase_order"]
    ticket_id: int
    reference_type: str | None = None
    reference_id: int | None = None
    account_name: str | None = None
    vendor_id: int | None = None
    sku: str | None = None
    size: str | None = None
    quantity: int | None = None
    amount: float = Field(ge=0)
    reason: str
    status: str
    created_at: str


class SpecialistReport(TrustedModel):
    agent: AgentRole
    ticket_id: int
    summary: str = Field(min_length=1, max_length=1200)
    facts: list[str] = Field(default_factory=list, max_length=12)
    blockers: list[str] = Field(default_factory=list, max_length=8)
    actions: list[str] = Field(default_factory=list, max_length=8)
    pending_approval_ids: list[int] = Field(default_factory=list, max_length=8)


class TicketResolution(TrustedModel):
    ticket_id: int
    category: TicketCategory
    status: TicketStatus
    resolution: str = Field(min_length=1, max_length=2000)
    tradeoff: str | None = Field(default=None, max_length=500)
    specialist_reports: list[SpecialistReport] = Field(default_factory=list, max_length=6)


class TicketRunResult(TrustedModel):
    ticket_id: int
    date_today: date
    decision: TicketResolution
    saved_ticket: dict[str, object]


class TeamRunResult(TrustedModel):
    processed: int
    results: list[TicketRunResult] = Field(default_factory=list, max_length=10)
    remaining_open_ticket_ids: list[int] = Field(default_factory=list, max_length=50)


class CustomerDraftRecord(TrustedModel):
    id: int
    ticket_id: int
    subject: str
    body: str
    status: Literal["draft"]
    created_at: str


class DashboardTicket(TrustedModel):
    id: int
    type: str
    requester: str
    subject: str
    sku: str | None = None
    size: str | None = None
    qty: int | None = None
    lease_id: int | None = None
    invoice_id: int | None = None
    status: str
    notes: str | None = None
    created_at: str
    is_open: bool
    is_resolved: bool


class DashboardTicketsResponse(TrustedModel):
    tickets: list[DashboardTicket]


class AgentEventRecord(TrustedModel):
    id: int
    time: str
    agent: AuditActor
    step: str
    tool_name: str = ""
    short_arguments: str
    short_result: str
    stop_reason: str = ""


class AgentEventsResponse(TrustedModel):
    events: list[AgentEventRecord]
    next_cursor: int
    total: int


class DashboardApprovalRecord(PendingApprovalRecord):
    payee: str | None = None
    vendor_name: str | None = None
    requested_by: str | None = None


class PendingApprovalsResponse(TrustedModel):
    approvals: list[DashboardApprovalRecord]


class CheckingBalanceResponse(TrustedModel):
    account_name: str
    balance: float
    date: str
    pending_obligations: float
    available_balance: float


class ApprovalRequest(TrustedModel):
    approved_by: str = Field(min_length=1, max_length=120)
    confirm: StrictBool


class ApprovalExecutionResult(TrustedModel):
    action: PendingApprovalRecord
    payment_id: int
    account_name: str
    new_balance: float


class ResetDatabaseRequest(TrustedModel):
    confirm: StrictBool


class ResetDatabaseResult(TrustedModel):
    restored_from: str
    working_database: str
    restored_ticket_ids: list[int]
    audit_trail_preserved: bool


class RejectionRequest(TrustedModel):
    rejected_by: str = Field(min_length=1, max_length=120)
    confirm: StrictBool
    reason: str | None = Field(default=None, max_length=500)


class RejectionResult(TrustedModel):
    action_id: int
    status: Literal["rejected"]
    ticket_status: str


class ShopTodayRecord(TrustedModel):
    date_today: date
    notes: str | None = None
