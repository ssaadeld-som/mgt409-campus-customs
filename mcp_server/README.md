# Campus Customs Operations MCP Server

FastMCP exposes trusted database tools for the Homework 5 multi-agent ticket team. It connects to `data/campus_customs_new.db` only. The original `data/campus_customs.db` is not used for agent writes.

## Tools

| Tool | Tables read | Tables written | Purpose and guardrails |
| --- | --- | --- | --- |
| `search_tickets` | `tickets` | — | Text/status/type search, capped at 50 rows. |
| `get_ticket` | `tickets`, `agent_board_notes`, `agent_actions` | — | Reads the ticket, current payment/PO action statuses, and the eight most recent board notes; includes a count of older notes omitted to limit model context. |
| `create_ticket` | `inventory`, `leases`, `invoices` | `tickets` | Validates supplied SKU/size and optional lease/invoice references before insert. |
| `update_ticket_status` | `tickets` | `tickets` | Updates an existing record only. |
| `add_ticket_note` | `tickets` | `tickets` | Appends while preserving earlier ticket notes. |
| `get_today` | `desk` | — | Reads the operational date; agents do not use the host date. |
| `list_open_tickets` | `tickets` | — | Returns only records with status `open`, capped at 50. |
| `list_pending_approvals` | `agent_actions` | — | Reads payment and purchase requests awaiting human approval. |
| `add_agent_note` | `tickets` | `agent_board_notes` | Appends specialist reports or decisions to a ticket board. |
| `resolve_ticket` | `tickets`, `agent_actions` | `tickets`, `agent_board_notes` | Records the Boss decision with `pending_approval`, `resolved`, or `blocked`; prevents resolving while approvals remain. |
| `check_stock` | `inventory` | — | Requires an exact SKU and size. |
| `find_vendors_for_sku` | `inventory`, `vendors` | — | Returns category candidates based on stored product names and vendor specialties. The schema has no formal SKU-vendor mapping, and output says so. |
| `vendor_status` | `vendors`, `invoices`, `desk`, `inventory` | — | Returns recorded lead time, open-invoice blockers, and expected arrival from desk date plus lead days. |
| `get_cash` | `cash_accounts`, `agent_actions` | — | Shows balances and pending obligations deducted from available cash. |
| `get_open_invoices` | `invoices`, `vendors`, `desk` | — | Lists unpaid invoices and computes overdue state against `desk.date_today`. |
| `get_leases` | `leases`, `desk` | — | Returns lease facts and due state using the operational date. |
| `describe_schema` | `sqlite_master`, table metadata | — | Shows real table/column names for grounded queries. |
| `run_select` | Any existing table | — | Allows one read-only `SELECT`, capped at 200 rows. |
| `request_payment` | `tickets`, `cash_accounts`, `invoices` or `leases`, `payments`, `agent_actions` | `agent_actions` | Requires an existing invoice or lease reference; checks recorded amount, cash, and duplicate requests/payments. Creates pending human approval only and never sends money. |
| `create_purchase_order` | `tickets`, `inventory`, `vendors`, `invoices`, `pricing`, `cash_accounts`, `agent_actions` | `agent_actions` | Creates a pending approval only; checks vendor blockers, category fit, SKU/size, cost, and cash. The request amount is recorded `pricing.unit_cost × quantity`, not a supplier quote; approval does not record delivery or add stock. |
| `save_draft` | `tickets` | `customer_drafts` | Saves a customer message as a draft; never sends it. |

The three operational tables (`agent_board_notes`, `agent_actions`, and `customer_drafts`) are created in the new database copy when the tools first run. They store handoffs, human approval requests, and unsent customer drafts. The MCP tools never write to `payments`, change cash balances, email customers, or contact vendors. Agent MCP tools do not retry failures; approval, draft, note, and delegation calls are serialized to avoid duplicate or conflicting writes.

## Run

From `Hmwrk 5`:

```bash
source .venv/bin/activate
pip install -r backend/requirements.txt
fastmcp run mcp_server/server.py:mcp
```

Codex can connect through the workspace `.mcp.json`. The PydanticAI agents call this same registered FastMCP server through its Python client, with role-specific tool access.
