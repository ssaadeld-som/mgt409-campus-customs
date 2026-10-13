# Homework 5 AI Prompts

## Problem 1: Vibe coder prompts

### Initial prompt

Today I am starting Homework 5. Please create this `AI_prompts.md` file, preserve the Homework 5 folder contents, and set up a local Python virtual environment in `.venv` for the assignment.

### Follow-up prompt

No follow-up prompt was needed for this problem.

## Problem 4: Add the MCP server to Codex and test each tool

### Initial prompt

Connect the Homework 5 FastMCP server through `.mcp.json`, then test all ticket tools against `data/campus_customs_new.db`. Find ticket 101 and report its status, create one new ticket with status `open`, update that ticket's status to `open`, and append the exact note `test ticket - Problem 4`. Save each prompt, tool name, arguments, and database-backed tool output in `output/mcp_smoke.json`.

### Follow-up prompt

No follow-up prompt was needed for this problem.

## Problem 3: Build the MCP server

### Initial prompt

Build a trusted FastMCP server in `mcp_server/` that uses `data/campus_customs_new.db`. Add tools to search tickets, retrieve a ticket by ID, create a ticket, update ticket status, and append a ticket note. Use only database-backed information, document the server and tools in its README, and update the harness with the tables/tools and how tickets 101, 102, and 103 are unlocked.

### Follow-up prompt

No follow-up prompt was needed for this problem.

## Problem 2: Study the Campus Customs database

### Initial prompt

Unzip and inspect the Campus Customs SQLite database. Copy the original database to `data/campus_customs_new.db`. Document every table and field in `output/harness.md`, explain why each field matters to the agents we will build, and explain how open rows in `tickets` relate to inventory, pricing, invoices, vendors, and leases.

### Follow-up prompt

No follow-up prompt was needed for this problem.

## Problem 5: Build the agent team and grow MCP tools

### Initial prompt

Build a five-agent PydanticAI ticket operations team (Boss, Inventory, Accounting, Facilities, and Customer Service) using the Portkey API key and only `gpt-6-luna`. Store one role prompt per agent in `backend/prompts/` and typed contracts in `backend/models.py`. Connect each role to its allowed FastMCP tools, implement ticket delegation and resolution, approvals-only payment and purchase workflows, and unsent customer drafts. Have agent steps and MCP tool results append to `output/audit_trail.json` across runs. Update the harness with agent roles, tool/table access, safety rules, model and loop limits, run commands, and update the MCP README tool list.

### Follow-up prompt

No follow-up prompt was needed for this problem.

## Problem 6: Plan the three tickets

### Initial prompt

Create `outputs/desktop_tickets.html` with tabs for the three operational tickets, Cash, and Reflection. Each ticket tab should show a centered, prominent “COMING SOON” title and separate Expected and Actual sections. Let me record expected agent outcomes now and actual outcomes later, after the agents run, so I can compare them.

### Follow-up prompt

Show the centered “COMING SOON” title only on the Cash and Reflection tabs, not on the ticket tabs. Keep Expected and Actual sections on each ticket tab for recording and later comparing the predicted and actual agent outcomes.
