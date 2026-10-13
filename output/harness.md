# Campus Customs database harness

Source: `data/campus_customs.db`, extracted from `data.zip` on 2026-10-06. The requested working copy is `data/campus_customs_new.db`. The database has 10 tables, 3 vendors, 10 inventory rows, 4 pricing rows, 3 tickets, 1 invoice, 1 lease, 1 cash account, and no payment rows.

## Tables and fields

### `cash_accounts`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `name` | TEXT, primary key | Identifies the cash account an operations agent may need to discuss. |
| `balance` | REAL, required | Gives the current account balance for grounded cash-status answers. |
| `date` | TEXT, required | Dates the balance so an agent does not present an old balance as current. |

### `desk`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `date_today` | TEXT, required | Supplies the shop's operational date for due-date and urgency reasoning. |
| `notes` | TEXT, optional | Preserves the daily desk context that may affect an operations response. |

### `inventory`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `sku` | TEXT, part of primary key | Joins a stock row to pricing and a ticket's requested product. |
| `name` | TEXT, required | Gives the agent the readable item name instead of requiring SKU-only answers. |
| `size` | TEXT, part of primary key | Identifies the exact size whose availability or quantity is being discussed. |
| `qty` | INTEGER, required | Provides the authoritative number of units; never infer stock from another table. |
| `location` | TEXT, required | Helps a staff-facing agent locate an item for fulfillment. |

The composite primary key `(sku, size)` means one stock record exists per product-size pair. Inventory has no declared foreign key, but `sku` conventionally joins `pricing.sku` and `tickets.sku`.

### `pricing`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `sku` | TEXT, primary key | Identifies the product whose cost and list price are being quoted. |
| `unit_cost` | REAL, required | Supports margin, purchasing, and internal cost questions. |
| `list_price` | REAL, required | Supplies the customer-facing price; it must not be invented or recalculated. |

### `vendors`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `id` | INTEGER, primary key | Identifies a vendor referenced by invoices. |
| `name` | TEXT, required | Names the supplier in purchasing or invoice answers. |
| `specialty` | TEXT, required | Indicates what the vendor supplies, helping route an operational request. |
| `lead_days` | INTEGER, required | Grounds realistic vendor timing without guessing. |

### `invoices`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `id` | INTEGER, primary key | Identifies an invoice and is referenced by tickets. |
| `vendor_id` | INTEGER, required, FK → `vendors.id` | Connects an amount and due date to the responsible supplier. |
| `amount` | REAL, required | Provides the exact payable amount. |
| `due_date` | TEXT, required | Lets an agent identify overdue or upcoming vendor obligations. |
| `status` | TEXT, required | Distinguishes open, paid, or other invoice states. |
| `description` | TEXT, optional | Explains what the invoice covers, such as a rush reprint. |

### `leases`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `id` | INTEGER, primary key | Identifies a shop lease and is referenced by rent tickets. |
| `space_name` | TEXT, required | Identifies the leased storefront or space. |
| `landlord` | TEXT, required | Names the party receiving rent. |
| `monthly_rent` | REAL, required | Supplies the recurring rent obligation. |
| `next_due` | TEXT, required | Grounds the next rent deadline. |
| `notes` | TEXT, optional | Stores lease-specific operating context. |

### `payments`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `id` | INTEGER, primary key | Identifies a payment for reconciliation. |
| `kind` | TEXT, required | Describes what the payment relates to, such as an invoice or rent. |
| `ref_id` | INTEGER, optional | Points to the related record ID; it is polymorphic rather than a declared FK. |
| `amount` | REAL, required | Provides the amount actually paid. |
| `account` | TEXT, required | Identifies the cash account used. |
| `paid_at` | TEXT, required | Dates the payment for reconciliation and audit answers. |
| `approved_by` | TEXT, required | Records the approving person for accountability. |

This source database currently has zero payment rows, so an agent must say no payment record is present rather than infer that an invoice was paid.

### `tickets`

| Field | Type/constraint | Why it matters to an agent |
| --- | --- | --- |
| `id` | INTEGER, primary key | Identifies a support or operations request for tracking. |
| `type` | TEXT, required | Tells the agent whether the ticket is a customer order, rent notice, price override, or another workflow. |
| `requester` | TEXT, required | Identifies who needs follow-up. |
| `subject` | TEXT, required | Gives a concise human-readable issue title. |
| `sku` | TEXT, optional | Connects an item-related ticket to inventory and pricing by SKU. |
| `size` | TEXT, optional | Captures the requested size for fulfillment or replenishment. |
| `qty` | INTEGER, optional | Captures requested units, such as a bulk order quantity. |
| `lease_id` | INTEGER, optional, FK → `leases.id` | Links rent notices to the lease and its due date. |
| `invoice_id` | INTEGER, optional, FK → `invoices.id` | Links an order or purchasing issue to vendor invoice details. |
| `status` | TEXT, required | Determines whether the agent should include the ticket in an open-work queue. |
| `notes` | TEXT, optional | Contains the operational detail needed for a useful next action. |
| `created_at` | TEXT, required | Orders tickets chronologically and supports aging/triage. |

There is no separate `open_tickets` table. “Open tickets” means rows in `tickets` where `status = 'open'`; all three current tickets are open.

### `tickets` relationships and current open work

- Ticket `101` (`customer_order`) requests one `CC-TEE-WHITE`, size S. Its `invoice_id = 501` joins to invoice 501, which joins through `vendor_id = 1` to Bulldog Print Co. The same SKU joins `inventory` (size S has quantity 0) and `pricing` (list price $28, unit cost $8). This lets an agent explain the stockout and the related rush-reprint invoice without inventing a resolution.
- Ticket `102` (`rent_notice`) has `lease_id = 1`, joining to the Chapel Street shop lease with Elm City Properties and next rent due 2026-09-02. It has no SKU or invoice.
- Ticket `103` (`price_override`) requests 20 `CC-HOOD-NAVY` hoodies in size M from Yale AI Club. Its SKU joins `inventory` (size M quantity 8) and `pricing` (list price $58, unit cost $22), so an agent can flag that requested quantity exceeds current stock and route the discount request for approval.

The declared foreign keys are `invoices.vendor_id → vendors.id`, `tickets.lease_id → leases.id`, and `tickets.invoice_id → invoices.id`. SKU joins between `inventory`, `pricing`, and `tickets` are logical/application joins, not declared SQLite foreign keys.

## Agent-use guardrails

- Query the relevant table(s) before answering; the database is the source of truth for stock, prices, invoices, leases, and ticket status.
- Preserve the distinction between customer-facing `list_price`, internal `unit_cost`, and actual `inventory.qty`.
- Treat an absent row, empty payment table, or missing optional relationship as `Not Found`/not recorded—not as permission to guess.
- For an open-ticket answer, filter `tickets.status = 'open'` and join only the appropriate SKU, lease, invoice, vendor, inventory, or pricing records.

## Problem 3: Campus Customs Tickets MCP server

### Server and database boundary

- The FastMCP server is implemented in `mcp_server/server.py` and exposes the server as `mcp` with the name **Campus Customs Tickets**.
- It connects only to `data/campus_customs_new.db`, the requested Homework 5 copy. It never opens or writes the extracted original `data/campus_customs.db`.
- SQLite connections enable foreign-key enforcement and use parameterized queries. Missing records return `Not Found` rather than a generated substitute.

### Tools

| Tool | Primary table(s) read/written | What it does |
| --- | --- | --- |
| `search_tickets` | Reads `tickets` | Finds real ticket rows by text, status, and/or type; results are bounded to 50. |
| `get_ticket` | Reads `tickets` | Retrieves one actual ticket by numeric ID. |
| `create_ticket` | Writes `tickets`; validates `inventory`, `leases`, and `invoices` when linked values are supplied | Creates an operational ticket from caller-provided facts and returns the saved row. |
| `update_ticket_status` | Reads and writes `tickets` | Updates the non-empty status of an existing ticket and returns the saved record. |
| `add_ticket_note` | Reads and writes `tickets` | Appends a non-empty note while retaining any existing notes. |

`create_ticket` only accepts a supplied SKU/size when it exists in `inventory`; it also checks supplied `lease_id` and `invoice_id` against the real linked tables. The timestamp is server-generated at creation time. The server does not create products, pricing, inventory, vendors, invoices, or leases.

### Existing tickets unlocked by the tools

| Ticket | Trusted context | Most relevant tool | Why that tool is right |
| --- | --- | --- | --- |
| `101` — customer order | `CC-TEE-WHITE`, size S, quantity 1, invoice 501, status open | `get_ticket` | It retrieves the full ticket record by its known ID so an agent can then use the real SKU/invoice references without guessing. |
| `102` — rent notice | Lease 1, Elm City Properties, status open | `add_ticket_note` | The ticket contains a follow-up message; appending a dated operational response preserves the original rent notice and its lease link. |
| `103` — price override | `CC-HOOD-NAVY`, size M, quantity 20, status open | `update_ticket_status` | A discount request needs workflow handling; a status update records its reviewed/approved state on the existing ticket rather than creating a duplicate. |

`search_tickets(status="open")` returns the complete current queue (101, 102, and 103) from the database. `create_ticket` handles a new verified request only when the caller supplies the facts; it is not used to recreate those existing tickets.

### Run and verification

From `Hmwrk 5`:

```bash
source .venv/bin/activate
pip install -r mcp_server/requirements.txt
fastmcp run mcp_server/server.py:mcp
```

`mcp_server/README.md` documents the same purpose, database path, guardrails, and tools. FastMCP inspection confirms five tools are registered; read verification returned ticket 101 (`Bulldog tee`) and the three open ticket rows.

## Problem 5: Five-agent ticket operations team

### Runtime and model

- `backend/agents.py` creates five PydanticAI agents: Boss, Inventory, Accounting, Facilities, and Customer Service. Each role prompt is stored separately in `backend/prompts/`; `common.md` supplies shared safety rules.
- All five use only `gpt-6-luna` through Portkey's OpenAI-compatible endpoint. `PORTKEY_API_KEY` loads from the course-root `.env` (or local Homework 5 `.env`) and is never logged.
- Typed contracts live in `backend/models.py`: operational records (`TicketRecord`, `StockRecord`, `VendorRecord`, `VendorStatusRecord`, `InvoiceRecord`, `LeaseRecord`, `CashAccountRecord`, `PendingApprovalRecord`), agent results (`SpecialistReport`, `TicketResolution`, `TicketRunResult`, `TeamRunResult`), and the structured audit entry `AuditTrailEvent`.
- Boss owns each ticket and delegates by domain. Inventory and Facilities may ask Accounting for a payment/cash dependency. Customer Service only drafts after it receives confirmed facts. All MCP requests use FastMCP's in-process client and the role-specific tool allowlist.

### Agent access, source tables, and safety

Shared policy: the desk table is the only date source; agents use recorded database facts and report missing values as `Not Found`/not recorded. There is no SKU-vendor relation, so vendor matches remain category candidates. Cash is outflow-only, cannot go negative, and is never moved by an agent; payments and purchase orders are human approvals. No revenue, prices, quantities, dates, products, or customer/vendor messages are invented. Ticket-specific actions stay within the assigned ticket.

| Agent | MCP tools available | Tables accessed through those tools | Safety guardrails |
| --- | --- | --- | --- |
| Boss | Shared: `get_today`, `get_ticket`, `add_agent_note`, `describe_schema`, `run_select`; plus `list_open_tickets`, `list_pending_approvals`, `resolve_ticket`; delegation tools `ask_inventory`, `ask_accounting`, `ask_facilities`, `ask_customer_service` | `desk`, `tickets`, `agent_board_notes`, `agent_actions`, schema metadata, and tables only through read-only `SELECT`; specialist tools use the source tables below | Must get operational date and ticket first; specialist work is delegated; checks pending approvals before closing; `resolve_ticket` is the final ticket tool action; status limited to pending approval/resolved/blocked; no resolution while approval remains. |
| Inventory | Shared tools; `check_stock`, `find_vendors_for_sku`, `vendor_status`; `ask_accounting` | `desk`, `tickets`, `agent_board_notes`, `inventory`, `vendors`, `invoices`; `run_select` is read-only | Exact SKU and size lookup; shortfall never negative; vendor blockers stop restocks; no payments or purchase-order approval; the schema has no exact vendor-SKU mapping, so specialty matches are labeled as candidates. |
| Accounting | Shared tools; `get_cash`, `get_open_invoices`, `request_payment`, `create_purchase_order` | `desk`, `tickets`, `agent_board_notes`, `cash_accounts`, `agent_actions`, `invoices`, `vendors`, `payments`, `leases`, `inventory`, `pricing` | Uses `pricing.list_price` and `pricing.unit_cost`; prompt policy forbids below-cost discounts and requires a recorded requested price; cash subtracts pending obligations; duplicate invoice/lease payments are refused; payment and purchase requests remain pending human approvals; no money moves. |
| Facilities | Shared tools; `get_leases`; `ask_accounting` | `desk`, `tickets`, `agent_board_notes`, `leases`; payment dependency is handled by Accounting | Uses desk date only; reports only recorded lease fields; absent terms, late fees, expirations, or renewal dates are stated as not recorded; never pays. |
| Customer Service | Shared tools; `check_stock`, `save_draft` | `desk`, `tickets`, `agent_board_notes`, `inventory`, `customer_drafts` | Drafts only from confirmed facts, checks exact requested size, saves to the database as an unsent draft, and never emails a customer. |

### MCP tools and database scope

| MCP tool | Tables read | Tables written / capability |
| --- | --- | --- |
| `get_today` | `desk` | Reads `date_today`; sole source for date reasoning. |
| `list_open_tickets`, `search_tickets` | `tickets` | Bounded ticket lists/search. |
| `get_ticket` | `tickets`, `agent_board_notes`, `agent_actions` | Reads the ticket, current approval-action state, and the eight most recent board notes; reports how many older notes were omitted to bound context. |
| `add_agent_note` | `tickets` | Appends to `agent_board_notes`. |
| `resolve_ticket` | `tickets`, `agent_actions` | Writes final ticket status and a Boss note; enforces outstanding approval status. |
| `check_stock` | `inventory` | Exact SKU/size quantity and location. |
| `find_vendors_for_sku` | `inventory`, `vendors` | Finds category candidates and returns the mapping limitation. |
| `vendor_status` | `vendors`, `invoices`, `desk`, `inventory` | Lead days, open-invoice shipping block, and expected arrival from desk date. |
| `get_cash` | `cash_accounts`, `agent_actions` | Balance and available cash after pending obligations. |
| `get_open_invoices` | `invoices`, `vendors`, `desk` | Unpaid invoice/vendor details and overdue flag. |
| `get_leases` | `leases`, `desk` | Lease due and overdue data. |
| `request_payment` | `tickets`, `cash_accounts`, `invoices` or `leases`, `payments`, `agent_actions` | Requires an existing invoice or lease reference; validates recorded amount, available cash, and duplicate requests/payments; writes a pending human approval only. |
| `create_purchase_order` | `tickets`, `inventory`, `vendors`, `invoices`, `pricing`, `cash_accounts`, `agent_actions` | Writes a pending purchase approval only; validates candidate, blocker, cost, and cash. |
| `list_pending_approvals` | `agent_actions` | Reads pending human decisions. |
| `save_draft` | `tickets` | Writes an unsent record to `customer_drafts`. |
| `describe_schema` | `sqlite_master`, table metadata | Reads actual schema. |
| `run_select` | Any existing table | One read-only `SELECT`, bounded to 200 rows. |
| `create_ticket`, `update_ticket_status`, `add_ticket_note` | `tickets`; `create_ticket` also checks `inventory`, `leases`, `invoices` | Ticket-only write operations from Problem 3. |

First tool use creates three workflow tables in `campus_customs_new.db`: `agent_board_notes` (specialist handoffs/resolutions), `agent_actions` (pending payment/purchase approvals), and `customer_drafts` (unsent customer messages). `payments`, cash balances, inventory quantities, invoices, vendors, and leases are never modified by these agent tools.

The agent orchestrator scopes ticket-specific MCP calls and specialist delegations to the ticket assigned for the current run. Attempts to read or write a different ticket are refused and audited. Independent read calls may run together; delegated work and write-capable agent tools are serialized. It caches the row returned by `resolve_ticket` for the run result, so no database lookup follows the required final ticket action.

### Loop bounds and audit trail

- The team uses `gpt-6-luna` through Portkey. Function-tool requests set `reasoning_effort` to `none` for endpoint compatibility. Boss is capped at 9 model requests and 24,000 tokens; specialists are capped at 7 requests and 14,000 tokens, except Accounting and Inventory at 20,000. Every agent has at most 12 tool calls; MCP tool timeout is 45 seconds. Output validation may retry once; MCP and delegation tools have zero retries. Each ticket permits at most 4 cumulative specialist delegations, and a batch processes no more than 10 tickets sequentially. Approval, draft, note, and delegation calls run serially to avoid conflicting writes. A failed SQLite `run_select` is recorded and returned as structured error evidence so a bad column/table query need not abort the whole ticket; transport failures still stop the run.
- Agents stop after a grounded decision, approval request, clarification, or concrete blocker. A missing relationship/value is named as not recorded rather than guessed. Failed identical calls are not retried.
- `backend/audit.py` appends each agent start/end/error, every PydanticAI graph-loop node as it is reached, delegation, scope refusal, human approval, and MCP tool start/result/error to `output/audit_trail.json`. Each event records UTC time, agent, step, tool/node name, and shortened arguments/result. Existing event bytes are retained while new events extend the JSON array; prompts and secrets are not stored. This working file contains the actual Homework 5 runs, tool errors, status changes, and approvals; do not clear or replace it.
- Run the team from `Hmwrk 5` with `source .venv/bin/activate` then `python -m backend.agents`. Install dependencies with `pip install -r backend/requirements.txt`. Run Codex-facing MCP separately with `fastmcp run mcp_server/server.py:mcp`.

## Problem 4: Codex MCP connection and smoke test

- `.mcp.json` registers the `campus-customs-tickets` server for Codex using the Homework 5 virtual environment's `fastmcp` executable and `mcp_server/server.py:mcp` entry point.
- `output/mcp_smoke.json` records the exact prompt, tool name, arguments, and returned structured output for every MCP tool.
- The smoke test found ticket `101`; its database status was `open`.
- The test created ticket `104` with type `system_test`, requester `Codex`, subject `Problem 4 MCP smoke test`, and status `open`. It then tested `update_ticket_status` by saving `open` again and `add_ticket_note` by appending the exact required note: `test ticket - Problem 4`.
- These write operations intentionally affect only `data/campus_customs_new.db`. The original extracted `data/campus_customs.db` remains unchanged.

## Problem 7: FastAPI dashboard backend

- `backend/main.py` exposes the dashboard API over the working copy `data/campus_customs_new.db`; it does not write to the preserved `data/campus_customs.db` except to read it when a confirmed reset is requested.
- `GET /api/tickets` — reads `tickets` and returns only tickets 101–103, including their current `status`, `is_open`, and `is_resolved` flags.
- `POST /api/tickets/{ticket_id}/run` — runs the existing bounded Boss-led PydanticAI workflow for one open dashboard ticket and returns its saved `TicketRunResult`.
- `GET /api/events?after={cursor}&limit={n}` — reads the append-only `output/audit_trail.json` and returns recent agent outputs, delegation notes, tool names/results, and the next incremental-refresh cursor.
- `GET /api/approvals` — reads pending rows from `agent_actions`, resolves payee/vendor from `invoices`, `leases`, and `vendors`, and attributes requesters from recorded MCP events.
- `POST /api/approvals/{action_id}/approve` — requires `{"approved_by":"...","confirm":true}`, rechecks the stored obligation/vendor/cash, writes a `payments` row, reduces `cash_accounts.balance`, and marks the action approved; an approved purchase is financially recorded but does not add stock before delivery.
- `POST /api/approvals/{action_id}/reject` — requires `{"rejected_by":"...","confirm":true}`, records the rejection in `agent_actions` and `agent_board_notes`, moves no cash, and marks a ticket blocked once it has no remaining pending approvals.
- `GET /api/cash` — reads the `checking` row in `cash_accounts` and reports its balance plus pending-only commitments and available cash.
- `GET /api/today` — reads `desk.date_today`, the single authoritative date used by the agents for overdue logic.
- `POST /api/reset` — requires `{"confirm":true}` and restores `data/campus_customs_new.db` from the untouched `data/campus_customs.db`; the cumulative audit trail is deliberately preserved.

The approval endpoint uses an SQLite `BEGIN IMMEDIATE` transaction, requires explicit confirmation and an approver name, refuses duplicate/non-pending or unaffordable actions, validates invoice/lease references and purchase SKU/vendor/cost against current rows, and cannot spend below zero. Only human approval through this API moves cash; agent MCP tools still create pending requests only. `get_cash`, payment requests, and purchase requests count only still-pending actions as obligations because approved amounts are already reflected in the cash balance.

Install backend dependencies from the `Hmwrk 5` folder with `pip install -r backend/requirements.txt`. Start the dashboard API using the requested command from `Hmwrk 5/backend`: `uvicorn main:app --reload --port 8000`. OpenAPI documentation is available at `/docs` while the server is running.

## Problem 8: “The Back Room” React dashboard

- `frontend/` is the Vite + React + TypeScript board; it reads tickets, approval slips, checking cash, `desk.date_today`, and audit events from FastAPI only—ticket facts, balances, and agent output are not fixture data in the UI.
- The API exposes `GET /api/today` for the authoritative shop date; approval slips include database-derived payee/vendor and `requested_by` from the tool-completion audit event, or leave the attribution blank if none was recorded.
- Dashboard events poll about once per second during a run; station statements/tool tags and the one-or-two-line run summary come from actual event results, and tickets display “resolved” only when the backend status is `resolved`.
- Approvals require a human name/initials and confirmation; `/approve` updates the payment ledger and checking balance, while `/reject` records a rejection without moving cash. Backend refusal details remain visible on the approval slip.
- `output/design.md` records the paper-and-ink print-shop design, three-zone layout, agent colors, motion/accessibility rules, and live-data behavior.
- Install once with `cd frontend && npm install`; start the board with `npm run dev`. `frontend/.env.example` targets API port 8000; the local ignored `.env.local` targets port 8001 while Homework 4 owns 8000. Vite uses the next free port when 5173 is already occupied.

## Problem 9: Ticket run results and cash reconciliation

- The working database was reset from the preserved original at the start of the Problem 9 workflow. That reset and all later events remain in `output/audit_trail.json`; this turn did not reset the database or truncate the audit trail.
- Final database statuses: ticket 101 `blocked`, ticket 102 `resolved`, ticket 103 `blocked`. The approval route itself only records the approval/payment; subsequent Boss runs set the ticket statuses. No approvals remain pending.
- Ticket 101: Inventory checked `CC-TEE-WHITE` size S and found 0 for 1 requested. Bulldog Print Co is an apparel-category candidate only (no formal SKU mapping); after invoice 501 was paid, vendor status showed no open invoice and a 5-day lead time. Accounting verified $8 SKU-level `pricing.unit_cost` and $160 cash, then created purchase-order approval #3 for one size-S unit; $8 is the tool's cost basis, not a vendor quote. User approved #3; payment row #3 reduced checking by $8. A later Inventory recheck still found 0 units and no receipt record. Boss marked the ticket blocked pending actual receipt. Customer Service was not called and no ticket-101 draft exists.
- Ticket 102: Facilities verified lease 1 rent of $2,400 due 2026-09-02, not overdue on desk date 2026-08-31. Accounting created payment approval #2; User approved it and payment row #2 paid the lease. Boss later verified the payment record, avoided a duplicate, and resolved ticket 102.
- Ticket 103: Accounting's saved analysis records $22 unit cost, $58 list price, and 62.07% list-price gross margin. No proposed discounted price is recorded, so discounted margin cannot be calculated or approved. Inventory reconfirmed 8 of 20 requested CC-HOOD-NAVY size M (shortfall 12). Customer Service saved draft #1 asking for the proposed unit price and explaining the stock shortage; it was not sent. No payment or purchase request was created. Boss marked the ticket blocked pending customer pricing input and a plan for the shortfall.
- Cash reconciliation from `cash_accounts` and `payments`: starting checking $3,400; ticket 101 invoice −$840 (payment row #1), ticket 102 rent −$2,400 (row #2), ticket 101 restock approval −$8 (row #3), ticket 103 $0; ending balance $152. The three approval rows total $3,248 and match the database: $3,400 − $840 − $2,400 − $8 = $152. All three actions were approved by User; pending approvals total $0. Approval #3 does not itself add stock.
- `outputs/desktop_tickets.html` preserves the Expected fields and records the latest known Actual results, status, delegation, tools, approvals, and cash. `outputs/resolved_tickets.json` includes all three literal database statuses, agent contributions, approval/payment IDs, the ticket-103 unsent draft, and cash movements; the requested filename is retained even though only ticket 102 is resolved.
- `output/resolved_board.html` summarizes the final database state and links the React board screenshot at `output/resolved_board.png`. That PNG is an earlier capture (before approval #3, the final ticket statuses, and the $152 balance); the page labels it as stale and links to the live board at `http://127.0.0.1:5175` while local services are running. The live React board at that URL was verified to show ticket 101 blocked, 102 resolved, 103 blocked, $152 checking, and an empty approvals tray.
- The agent workflow for final runs used FastAPI on local port 8002; the Vite dashboard is on 5175 and reads the same working database through its local API configuration. Normal commands remain `uvicorn main:app --reload --port 8000` from `backend/` and `npm run dev` from `frontend/`. API routes are listed under Problem 7 above.
- Bounds now are Boss 9 requests / 24,000 tokens; specialists 7 requests and 14,000 tokens except Accounting and Inventory at 20,000; 12 tool calls per agent; 4 cumulative specialist delegations per ticket; 45-second MCP tool timeout; 10-ticket batch cap. Repeated historical error events are retained in the audit; the finite limits prevent unbounded retries.
