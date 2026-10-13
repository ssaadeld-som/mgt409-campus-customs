import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

type AgentName = "Boss" | "Inventory" | "Accounting" | "Facilities" | "Customer Service";
type AgentEvent = {
  id: number;
  time: string;
  agent: AgentName | "Orchestrator";
  step: string;
  tool_name: string;
  short_arguments: string;
  short_result: string;
  stop_reason: string;
};
type Ticket = {
  id: number;
  type: string;
  requester: string;
  subject: string;
  sku: string | null;
  size: string | null;
  qty: number | null;
  lease_id: number | null;
  invoice_id: number | null;
  status: string;
  notes: string | null;
  created_at: string;
  is_open: boolean;
  is_resolved: boolean;
};
type Approval = {
  id: number;
  action_type: "payment" | "purchase_order";
  ticket_id: number;
  reference_type: string | null;
  reference_id: number | null;
  account_name: string | null;
  vendor_id: number | null;
  sku: string | null;
  size: string | null;
  quantity: number | null;
  amount: number;
  reason: string;
  status: string;
  created_at: string;
  payee: string | null;
  vendor_name: string | null;
  requested_by: AgentName | null;
};
type Balance = {
  account_name: string;
  balance: number;
  date: string;
  pending_obligations: number;
  available_balance: number;
};
type ShopToday = { date_today: string; notes: string | null };
type EventPage = { events: AgentEvent[]; next_cursor: number; total: number };
type RunResult = {
  ticket_id: number;
  date_today: string;
  decision: {
    category: string;
    status: string;
    resolution: string;
    tradeoff: string | null;
    specialist_reports: Array<{ agent: AgentName; summary: string; facts: string[]; actions: string[]; blockers: string[] }>;
  };
  saved_ticket: Record<string, unknown>;
};
type RunSnapshot = {
  events: AgentEvent[];
  result: { ticket_id: number; saved_ticket: Record<string, unknown> };
};

const AGENTS: AgentName[] = ["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"];
const AGENT_META: Record<AgentName, { color: string; monogram: string; station: string }> = {
  Boss: { color: "boss", monogram: "B", station: "Front counter" },
  Inventory: { color: "inventory", monogram: "I", station: "Stockroom" },
  Accounting: { color: "accounting", monogram: "A", station: "Books & bills" },
  Facilities: { color: "facilities", monogram: "F", station: "Shop floor" },
  "Customer Service": { color: "service", monogram: "C", station: "Customer notes" },
};

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new Error(`Can’t reach the Campus Customs API at ${API_BASE}. Check that the backend is running.`);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`;
    throw new Error(detail);
  }
  return body as T;
}

function parseLoggedJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function loggedStringField(text: string, field: string): string | null {
  const match = text.match(new RegExp(`"${field}"\\s*:\\s*"((?:\\\\.|[^"\\\\])*)"`));
  if (!match) return null;
  try {
    return JSON.parse(`"${match[1]}"`) as string;
  } catch {
    return match[1];
  }
}

function objectFromLog(text: string): Record<string, unknown> | null {
  const parsed = parseLoggedJson(text);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
}

function money(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function prettyDate(value: string | null | undefined): string {
  if (!value) return "Date not available";
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" }).format(date);
}

function eventTicketId(event: AgentEvent): number | null {
  const args = objectFromLog(event.short_arguments);
  const id = args?.ticket_id;
  return typeof id === "number" ? id : null;
}

function snapshotsFromAudit(events: AgentEvent[], tickets: Ticket[]): Record<number, RunSnapshot> {
  const runs = new Map<number, AgentEvent[]>();
  let activeTicketId: number | null = null;
  for (const event of events) {
    if (event.step === "dashboard_agent_run_requested") {
      activeTicketId = eventTicketId(event);
      if (activeTicketId !== null) runs.set(activeTicketId, [event]);
      continue;
    }
    if (activeTicketId !== null) {
      runs.get(activeTicketId)?.push(event);
      if (event.step === "ticket_workflow_completed" || event.step === "dashboard_agent_run_error") activeTicketId = null;
    }
  }
  return Object.fromEntries([...runs.entries()].map(([ticketId, runEvents]) => {
    const ticket = tickets.find((item) => item.id === ticketId);
    return [ticketId, {
      events: runEvents,
      result: { ticket_id: ticketId, saved_ticket: { status: ticket?.status ?? "not recorded" } },
    }];
  }));
}

function resultText(event: AgentEvent): string {
  const result = objectFromLog(event.short_result);
  if (event.step === "agent_completed" && result) {
    const text = typeof result.summary === "string" ? result.summary : typeof result.resolution === "string" ? result.resolution : "";
    if (text) return text;
  }
  if (event.step === "agent_completed") {
    const text = loggedStringField(event.short_result, "summary") ?? loggedStringField(event.short_result, "resolution");
    if (text) return text;
  }
  if (event.step === "delegation_started") {
    const args = objectFromLog(event.short_arguments);
    if (typeof args?.question === "string") return args.question;
  }
  if (event.step === "mcp_tool_completed") {
    const compact = result ? JSON.stringify(result) : event.short_result;
    return `${event.tool_name} returned ${compact}`;
  }
  if (event.step === "mcp_tool_started") return `Calling ${event.tool_name}`;
  if (event.step === "delegation_completed" && result && typeof result.summary === "string") return result.summary;
  if (event.step === "delegation_completed") return loggedStringField(event.short_result, "summary") ?? "Returned a specialist report.";
  if (event.step === "agent_error") return event.short_result;
  if (event.step === "agent_started") return "Started the assigned ticket run.";
  if (event.step === "model_loop_step") return "Reviewing the next step against the ticket and tool results.";
  if (event.step === "delegation_started") return `Handing a question to ${event.tool_name.replace("ask_", "").replaceAll("_", " ")}.`;
  if (event.step === "delegation_completed") return "Returned a specialist report to the delegating agent.";
  if (event.step === "agent_completed") return "Finished this part of the ticket.";
  return event.short_result || event.step.replaceAll("_", " ");
}

function stateForAgent(agent: AgentName, events: AgentEvent[], running: boolean): string {
  const latest = events.filter((event) => event.agent === agent).at(-1);
  if (!latest) return running ? "idle" : "idle";
  if (agent === "Boss" && latest.step === "agent_error" && events.some((event) => event.agent === "Boss" && event.step === "mcp_tool_completed" && event.tool_name === "resolve_ticket")) return "idle";
  if (latest.step === "mcp_tool_started") return "using a tool";
  if (latest.step === "delegation_started") return "waiting";
  if (latest.step === "agent_completed") return "idle";
  if (latest.step === "agent_error") return "waiting";
  if (latest.step === "agent_started" || latest.step === "model_loop_step" || latest.step === "mcp_tool_completed" || latest.step === "delegation_completed") {
    return running ? "thinking" : "idle";
  }
  return running ? "thinking" : "idle";
}

function latestStatement(agent: AgentName, events: AgentEvent[], running: boolean): string {
  const latest = events.filter((event) => event.agent === agent).at(-1);
  if (!latest) return running ? "Waiting for this station’s turn." : "No recent activity on this ticket.";
  if (agent === "Boss" && latest.step === "agent_error") {
    const persisted = bossPersistedSummary(events);
    if (persisted) return persisted;
  }
  return resultText(latest);
}

function toolTags(agent: AgentName, events: AgentEvent[]): string[] {
  return [...new Set(events.filter((event) => event.agent === agent && event.step === "mcp_tool_started").map((event) => event.tool_name))].slice(-4);
}

function agentFromDelegation(toolName: string): AgentName | null {
  const normalized = toolName.replace("ask_", "").replaceAll("_", " ").toLowerCase();
  return AGENTS.find((agent) => agent.toLowerCase() === normalized) ?? null;
}

function bossPersistedSummary(events: AgentEvent[]): string | null {
  const saved = events.filter((event) => event.agent === "Boss" && event.step === "mcp_tool_completed" && event.tool_name === "resolve_ticket").at(-1);
  const savedTicket = saved ? objectFromLog(saved.short_result) : null;
  if (!savedTicket || typeof savedTicket.status !== "string") return null;

  const specialists = [...new Set(events
    .filter((event) => event.agent === "Boss" && event.step === "delegation_started")
    .map((event) => agentFromDelegation(event.tool_name))
    .filter((agent): agent is AgentName => agent !== null))];
  const delegation = specialists.length ? `delegated to ${specialists.join(" and ")}` : "reviewed the ticket and recorded facts";
  const status = savedTicket.status.replaceAll("_", " ");
  const failedAfterWrite = events.some((event) => event.step === "dashboard_agent_run_error" || (event.agent === "Boss" && event.step === "agent_error"));
  const suffix = failedAfterWrite
    ? " The database write succeeded, but the final model response hit its usage limit."
    : " The saved status is from the database.";
  return `Boss read the ticket, ${delegation}, and saved status “${status}”.${suffix}`;
}

function activeHandoff(events: AgentEvent[]): { from: AgentName; to: AgentName; question: string; key: string } | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.step !== "delegation_started" || !AGENTS.includes(event.agent as AgentName)) continue;
    const completed = events.slice(index + 1).some((later) => later.step === "delegation_completed" && later.tool_name === event.tool_name);
    if (completed) continue;
    const target = agentFromDelegation(event.tool_name);
    const args = objectFromLog(event.short_arguments);
    if (!target) continue;
    return {
      from: event.agent as AgentName,
      to: target,
      question: typeof args?.question === "string" ? args.question : "Specialist question recorded in the activity log.",
      key: `${event.id}-${event.tool_name}`,
    };
  }
  return null;
}

function summaryLines(agent: AgentName, events: AgentEvent[]): string[] | null {
  const completed = events.filter((event) => event.agent === agent && event.step === "agent_completed").at(-1);
  const output = completed ? objectFromLog(completed.short_result) : null;
  if (!output && agent === "Boss") {
    const persisted = bossPersistedSummary(events);
    if (persisted) return [persisted];
    const agentResolution = completed ? loggedStringField(completed.short_result, "resolution") : null;
    return typeof agentResolution === "string" ? [agentResolution] : null;
  }
  if (!output) {
    const summary = completed ? loggedStringField(completed.short_result, "summary") : null;
    return summary ? [summary] : null;
  }
  if (agent === "Boss") {
    return [
      typeof output.resolution === "string" ? output.resolution : "",
      typeof output.tradeoff === "string" && output.tradeoff ? `Trade-off: ${output.tradeoff}` : "",
    ].filter(Boolean).slice(0, 2);
  }
  const lines: string[] = [];
  if (typeof output.summary === "string") lines.push(output.summary);
  const actions = Array.isArray(output.actions) ? output.actions.filter((item): item is string => typeof item === "string") : [];
  const blockers = Array.isArray(output.blockers) ? output.blockers.filter((item): item is string => typeof item === "string") : [];
  const facts = Array.isArray(output.facts) ? output.facts.filter((item): item is string => typeof item === "string") : [];
  const secondLine = actions[0] ? `Action: ${actions[0]}` : blockers[0] ? `Blocker: ${blockers[0]}` : facts[0] ?? "";
  if (secondLine) lines.push(secondLine);
  return lines.slice(0, 2);
}

function statusLabel(ticket: Ticket, activeTicketId: number | null): string {
  if (activeTicketId === ticket.id) return "working";
  if (ticket.status === "resolved") return "resolved";
  if (ticket.status === "pending_approval") return "needs approval";
  if (ticket.status === "blocked") return "blocked";
  return ticket.status || "unknown";
}

function labelForStatus(status: unknown): string {
  if (status === "pending_approval") return "needs approval";
  if (typeof status === "string") return status.replaceAll("_", " ");
  return "status not recorded";
}

function ApprovalSlip({
  approval,
  approver,
  busy,
  flash,
  error,
  onApprove,
  onReject,
}: {
  approval: Approval;
  approver: string;
  busy: boolean;
  flash: boolean;
  error?: string;
  onApprove: () => void;
  onReject: () => void;
}) {
  const label = approval.action_type === "payment" ? "Payment" : "Purchase order";
  const payee = approval.payee || approval.vendor_name || (approval.reference_id ? `${approval.reference_type ?? "Reference"} #${approval.reference_id}` : "Payee not recorded");
  return (
    <article className={`approval-slip ${flash ? "approval-slip--stamped" : ""}`}>
      <div className="slip-topline"><span>{label} · #{approval.id}</span><span>Ticket {approval.ticket_id}</span></div>
      <h3>{payee}</h3>
      {approval.vendor_name && approval.payee !== approval.vendor_name && <p className="slip-subline">Vendor · {approval.vendor_name}</p>}
      {approval.sku && <p className="slip-subline">{approval.sku}{approval.size ? ` · size ${approval.size}` : ""}{approval.quantity ? ` · qty ${approval.quantity}` : ""}</p>}
      <div className="approval-amount">{money(approval.amount)}</div>
      <p className="approval-reason">{approval.reason}</p>
      <p className="requested-by">Requested by <strong>{approval.requested_by || "Agent not recorded"}</strong></p>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <div className="approval-actions">
        <button type="button" className="button button--approve" onClick={onApprove} disabled={busy || !approver.trim()} title={!approver.trim() ? "Enter your name or initials first" : undefined}>
          {busy ? "Saving…" : "Approve"}
        </button>
        <button type="button" className="button button--reject" onClick={onReject} disabled={busy || !approver.trim()} title={!approver.trim() ? "Enter your name or initials first" : undefined}>Reject</button>
      </div>
    </article>
  );
}

export default function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [checking, setChecking] = useState<Balance | null>(null);
  const [shopToday, setShopToday] = useState<ShopToday | null>(null);
  const [activityEvents, setActivityEvents] = useState<AgentEvent[]>([]);
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([]);
  const runEventsRef = useRef<AgentEvent[]>([]);
  const eventCursor = useRef(0);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [runSummaries, setRunSummaries] = useState<Record<number, RunSnapshot>>({});
  const [initialLoading, setInitialLoading] = useState(true);
  const [backendErrors, setBackendErrors] = useState<string[]>([]);
  const [activityError, setActivityError] = useState("");
  const [runError, setRunError] = useState("");
  const [approver, setApprover] = useState("");
  const [busyApprovalId, setBusyApprovalId] = useState<number | null>(null);
  const [approvalErrors, setApprovalErrors] = useState<Record<number, string>>({});
  const [approvedFlashId, setApprovedFlashId] = useState<number | null>(null);
  const [resetting, setResetting] = useState(false);
  const [refreshNote, setRefreshNote] = useState("");
  const [displayBalance, setDisplayBalance] = useState<number | null>(null);
  const previousBalance = useRef<number | null>(null);

  const mergeActivity = useCallback((incoming: AgentEvent[]) => {
    if (!incoming.length) return;
    setActivityEvents((current) => {
      const events = new Map(current.map((event) => [event.id, event]));
      incoming.forEach((event) => events.set(event.id, event));
      return [...events.values()].sort((a, b) => a.id - b.id).slice(-80);
    });
  }, []);

  const mergeRunEvents = useCallback((incoming: AgentEvent[]) => {
    if (!incoming.length) return;
    const events = new Map(runEventsRef.current.map((event) => [event.id, event]));
    incoming.forEach((event) => events.set(event.id, event));
    const merged = [...events.values()].sort((a, b) => a.id - b.id);
    runEventsRef.current = merged;
    setRunEvents(merged);
  }, []);

  const refreshBoard = useCallback(async (showLoading = false) => {
    if (showLoading) setInitialLoading(true);
    const results = await Promise.allSettled([
      api<{ tickets: Ticket[] }>("/api/tickets"),
      api<{ approvals: Approval[] }>("/api/approvals"),
      api<Balance>("/api/cash"),
      api<ShopToday>("/api/today"),
      api<EventPage>("/api/events?limit=500"),
    ]);
    const labels = ["tickets", "approvals", "checking balance", "shop date", "activity tape"];
    const failures: string[] = [];
    const [ticketResult, approvalResult, cashResult, todayResult, eventResult] = results;
    if (ticketResult.status === "fulfilled") {
      setTickets(ticketResult.value.tickets);
      setSelectedId((current) => current ?? ticketResult.value.tickets[0]?.id ?? null);
    } else failures.push(`${labels[0]}: ${ticketResult.reason instanceof Error ? ticketResult.reason.message : "unavailable"}`);
    if (approvalResult.status === "fulfilled") setApprovals(approvalResult.value.approvals);
    else failures.push(`${labels[1]}: ${approvalResult.reason instanceof Error ? approvalResult.reason.message : "unavailable"}`);
    if (cashResult.status === "fulfilled") setChecking(cashResult.value);
    else failures.push(`${labels[2]}: ${cashResult.reason instanceof Error ? cashResult.reason.message : "unavailable"}`);
    if (todayResult.status === "fulfilled") setShopToday(todayResult.value);
    else failures.push(`${labels[3]}: ${todayResult.reason instanceof Error ? todayResult.reason.message : "unavailable"}`);
    if (eventResult.status === "fulfilled") {
      mergeActivity(eventResult.value.events);
      eventCursor.current = eventResult.value.next_cursor;
      if (ticketResult.status === "fulfilled") {
        const persistedRuns = snapshotsFromAudit(eventResult.value.events, ticketResult.value.tickets);
        setRunSummaries((current) => ({ ...current, ...persistedRuns }));
      }
    } else failures.push(`${labels[4]}: ${eventResult.reason instanceof Error ? eventResult.reason.message : "unavailable"}`);
    setBackendErrors(failures);
    setInitialLoading(false);
  }, [mergeActivity]);

  const pollNewEvents = useCallback(async () => {
    const page = await api<EventPage>(`/api/events?after=${eventCursor.current}&limit=200`);
    eventCursor.current = page.next_cursor;
    mergeActivity(page.events);
    mergeRunEvents(page.events);
    return page.events;
  }, [mergeActivity, mergeRunEvents]);

  useEffect(() => {
    void refreshBoard(true);
  }, [refreshBoard]);

  useEffect(() => {
    if (activeRunId === null) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        await pollNewEvents();
        setActivityError("");
      } catch (error) {
        if (!cancelled) setActivityError(error instanceof Error ? error.message : "Activity refresh failed.");
      }
      if (!cancelled) timer = window.setTimeout(poll, 1000);
    };
    timer = window.setTimeout(poll, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeRunId, pollNewEvents]);

  useEffect(() => {
    if (checking === null) return;
    const target = checking.balance;
    const start = previousBalance.current;
    previousBalance.current = target;
    if (start === null || start === target || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplayBalance(target);
      return;
    }
    const began = performance.now();
    const duration = 720;
    let animation = 0;
    const frame = (now: number) => {
      const progress = Math.min(1, (now - began) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplayBalance(start + (target - start) * eased);
      if (progress < 1) animation = window.requestAnimationFrame(frame);
    };
    animation = window.requestAnimationFrame(frame);
    return () => window.cancelAnimationFrame(animation);
  }, [checking?.balance]);

  const selectedTicket = useMemo(() => tickets.find((ticket) => ticket.id === selectedId) ?? null, [tickets, selectedId]);
  const shownRunId = activeRunId ?? selectedId;
  const visibleRun = activeRunId !== null ? runEvents : (shownRunId !== null ? runSummaries[shownRunId]?.events ?? [] : []);
  const selectedSnapshot = shownRunId !== null ? runSummaries[shownRunId] : undefined;
  const handoff = activeHandoff(visibleRun);

  async function startRun() {
    if (!selectedTicket || activeRunId !== null || !selectedTicket.is_open) return;
    setRunError("");
    setActivityError("");
    try {
      const baseline = await api<EventPage>("/api/events?limit=1");
      eventCursor.current = baseline.next_cursor;
    } catch (error) {
      setActivityError(error instanceof Error ? error.message : "Could not establish the activity cursor.");
    }
    runEventsRef.current = [];
    setRunEvents([]);
    setActiveRunId(selectedTicket.id);
    try {
      const result = await api<RunResult>(`/api/tickets/${selectedTicket.id}/run`, { method: "POST" });
      try {
        await pollNewEvents();
      } catch (error) {
        setActivityError(error instanceof Error ? error.message : "Final activity refresh failed.");
      }
      const savedStatus = typeof result.saved_ticket.status === "string" ? result.saved_ticket.status : result.decision.status;
      setRunSummaries((current) => ({ ...current, [selectedTicket.id]: { events: runEventsRef.current, result } }));
      setRefreshNote(savedStatus === "resolved" ? `Ticket ${selectedTicket.id} was resolved by the team.` : savedStatus === "pending_approval" ? `Ticket ${selectedTicket.id} is waiting for human approval.` : `Ticket ${selectedTicket.id} finished with status: ${savedStatus}.`);
      await refreshBoard(false);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "The agent team could not complete this ticket.");
      try {
        await pollNewEvents();
      } catch {
        // The primary run error remains visible; a failed secondary poll should not replace it.
      }
      await refreshBoard(false);
    } finally {
      setActiveRunId(null);
    }
  }

  async function approve(approval: Approval) {
    if (!approver.trim() || busyApprovalId !== null) return;
    const payee = approval.payee || approval.vendor_name || "this recorded obligation";
    if (!window.confirm(`Approve ${money(approval.amount)} for ${payee}? This will reduce the checking balance.`)) return;
    setBusyApprovalId(approval.id);
    setApprovalErrors((current) => ({ ...current, [approval.id]: "" }));
    try {
      await api(`/api/approvals/${approval.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ approved_by: approver.trim(), confirm: true }),
      });
      setApprovedFlashId(approval.id);
      window.setTimeout(() => setApprovedFlashId((current) => current === approval.id ? null : current), 1050);
      setRefreshNote(`${money(approval.amount)} approved for ${payee}. Checking balance refreshed.`);
      await refreshBoard(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Approval was refused.";
      setApprovalErrors((current) => ({ ...current, [approval.id]: message }));
      await refreshBoard(false);
    } finally {
      setBusyApprovalId(null);
    }
  }

  async function reject(approval: Approval) {
    if (!approver.trim() || busyApprovalId !== null) return;
    if (!window.confirm(`Reject ${approval.action_type === "payment" ? "payment" : "purchase order"} #${approval.id}? No cash will move.`)) return;
    const reason = window.prompt("Optional note for the rejection:") ?? "";
    setBusyApprovalId(approval.id);
    setApprovalErrors((current) => ({ ...current, [approval.id]: "" }));
    try {
      await api(`/api/approvals/${approval.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ rejected_by: approver.trim(), confirm: true, reason: reason.trim() || null }),
      });
      setRefreshNote(`Approval #${approval.id} rejected. No cash was moved.`);
      await refreshBoard(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Rejection was refused.";
      setApprovalErrors((current) => ({ ...current, [approval.id]: message }));
      await refreshBoard(false);
    } finally {
      setBusyApprovalId(null);
    }
  }

  async function resetDatabase() {
    if (activeRunId !== null || resetting) return;
    const confirmed = window.confirm("Reset the working database to the original Homework 5 values? This removes ticket decisions and pending approvals. The append-only audit trail is preserved.");
    if (!confirmed) return;
    setResetting(true);
    setRunError("");
    try {
      await api("/api/reset", { method: "POST", body: JSON.stringify({ confirm: true }) });
      setRunSummaries({});
      setRunEvents([]);
      runEventsRef.current = [];
      setApprovalErrors({});
      setRefreshNote("Working database restored from the original values; the audit tape was preserved.");
      await refreshBoard(false);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "The database could not be reset.");
    } finally {
      setResetting(false);
    }
  }

  const selectedCanRun = !!selectedTicket?.is_open && activeRunId === null;

  return (
    <div className="backroom-shell">
      <header className="masthead">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span>CC</span><i /></div>
          <div>
            <p className="eyebrow">Campus Customs · operations room</p>
            <h1>The Back Room</h1>
            <p className="brand-subtitle">Orders, stock, and decisions — on the shop floor.</p>
          </div>
        </div>
        <div className="masthead-actions">
          <div className="today-slip"><span className="slip-label">SHOP DATE</span><strong>{shopToday ? prettyDate(shopToday.date_today) : "Date loading…"}</strong></div>
          <button type="button" className="button button--quiet reset-button" onClick={() => void resetDatabase()} disabled={resetting || activeRunId !== null}>
            <span className="button-glyph" aria-hidden="true">↺</span>{resetting ? "Resetting…" : "Reset board"}
          </button>
        </div>
      </header>

      {backendErrors.length > 0 && <div className="notice notice--error" role="alert"><strong>Some panels need a connection.</strong> {backendErrors.join(" · ")}</div>}
      {runError && <div className="notice notice--error" role="alert"><strong>Agent run:</strong> {runError}</div>}
      {refreshNote && <div className="notice notice--success" role="status">{refreshNote}<button aria-label="Dismiss message" onClick={() => setRefreshNote("")}>×</button></div>}

      <main className="workbench-layout">
        <section className="panel tickets-panel" aria-labelledby="tickets-heading">
          <div className="section-heading">
            <div><span className="section-kicker">01 · Incoming work</span><h2 id="tickets-heading">Ticket rail</h2></div>
            <span className="count-badge">{tickets.length}</span>
          </div>
          <p className="panel-intro">Select a slip to bring it to the desk.</p>
          <div className="ticket-stack" aria-label="Tickets from the database">
            {initialLoading && tickets.length === 0 ? <div className="empty-paper">Pulling the work order book…</div> : null}
            {!initialLoading && tickets.length === 0 ? <div className="empty-paper">No tickets came back from the backend.</div> : null}
            {tickets.map((ticket) => {
              const status = statusLabel(ticket, activeRunId);
              return (
                <button
                  type="button"
                  className={`ticket-slip ${selectedId === ticket.id ? "ticket-slip--selected" : ""}`}
                  key={ticket.id}
                  aria-pressed={selectedId === ticket.id}
                  disabled={activeRunId !== null}
                  onClick={() => setSelectedId(ticket.id)}
                >
                  <div className="ticket-slip__top"><span className="ticket-id">#{ticket.id}</span><span className={`status-stamp status-stamp--${status.replaceAll(" ", "-")}`}>{status}</span></div>
                  <strong className="ticket-subject">{ticket.subject}</strong>
                  <span className="ticket-requester">{ticket.requester}</span>
                  {ticket.sku && <span className="ticket-product">{ticket.sku}{ticket.size ? ` · ${ticket.size}` : ""}</span>}
                  <span className="ticket-rule" aria-hidden="true" />
                  <span className="slip-printed">ORDER SLIP · {prettyDate(ticket.created_at)}</span>
                </button>
              );
            })}
          </div>
          <div className="ticket-rail-foot"><span className="tiny-dot" />Showing the three operational tickets from the shop database</div>
        </section>

        <section className="panel desk-panel" aria-labelledby="desk-heading">
          <div className="section-heading desk-heading">
            <div><span className="section-kicker">02 · Team workspace</span><h2 id="desk-heading">The agent desk</h2></div>
            <span className={`live-light ${activeRunId !== null ? "live-light--on" : ""}`}><i />{activeRunId !== null ? "ON THE CLOCK" : "STANDING BY"}</span>
          </div>
          <div className="selected-ticket-strip">
            {selectedTicket ? <>
              <div><span className="selected-ticket-id">TICKET {selectedTicket.id}</span><h3>{selectedTicket.subject}</h3><p>{selectedTicket.notes || "No note is recorded for this ticket."}</p></div>
              <div className="selected-facts">
                {selectedTicket.sku && <span><b>SKU</b>{selectedTicket.sku}</span>}
                {selectedTicket.size && <span><b>SIZE</b>{selectedTicket.size}</span>}
                {selectedTicket.qty !== null && <span><b>QTY</b>{selectedTicket.qty}</span>}
                {selectedTicket.lease_id !== null && <span><b>LEASE</b>#{selectedTicket.lease_id}</span>}
                {selectedTicket.invoice_id !== null && <span><b>INVOICE</b>#{selectedTicket.invoice_id}</span>}
              </div>
            </> : <div className="empty-selection">Select a ticket from the rail.</div>}
          </div>

          {handoff && (
            <div className="handoff-ribbon" key={handoff.key} role="status">
              <div className="handoff-route"><span className={`handoff-agent agent-tone--${AGENT_META[handoff.from].color}`}>{handoff.from}</span><span className="handoff-wire" aria-hidden="true"><i /></span><span className={`handoff-agent agent-tone--${AGENT_META[handoff.to].color}`}>{handoff.to}</span></div>
              <p><span className="quote-mark">“</span>{handoff.question}<span className="quote-mark">”</span></p>
            </div>
          )}

          <div className="agent-grid" aria-label="Five agent stations">
            {AGENTS.map((agent) => {
              const meta = AGENT_META[agent];
              const state = stateForAgent(agent, visibleRun, activeRunId !== null);
              const tools = toolTags(agent, visibleRun);
              return (
                <article className={`agent-station agent-station--${meta.color} ${agent === "Boss" ? "agent-station--boss" : ""}`} key={agent}>
                  <div className="station-header">
                    <span className="agent-seal">{meta.monogram}</span>
                    <div className="station-name"><h3>{agent}</h3><span>{meta.station}</span></div>
                    <span className={`station-state station-state--${state.replaceAll(" ", "-")}`}>{state}</span>
                  </div>
                  <p className="station-statement">{latestStatement(agent, visibleRun, activeRunId !== null)}</p>
                  <div className="tool-tags" aria-label={`${agent} tool calls`}>
                    {tools.length ? tools.map((tool) => <span className="tool-tag" key={tool}>{tool}</span>) : <span className="no-tool-tag">No tool call yet</span>}
                  </div>
                </article>
              );
            })}
          </div>

          <div className="desk-actions">
            <div className="desk-action-copy"><span className="button-eyebrow">Selected work order</span><strong>{selectedTicket ? `#${selectedTicket.id} · ${selectedTicket.subject}` : "Choose a ticket first"}</strong></div>
            <button type="button" className="button button--primary run-button" onClick={() => void startRun()} disabled={!selectedCanRun}>
              {activeRunId !== null ? <><span className="spinner" aria-hidden="true" />Team working…</> : "Run agents"}<span aria-hidden="true">→</span>
            </button>
          </div>
          {!selectedCanRun && activeRunId === null && selectedTicket && <p className="run-hint">This ticket is “{statusLabel(selectedTicket, null)}”; only open tickets can be run.</p>}

          {selectedSnapshot && (
            <section className="summary-card" aria-labelledby="summary-heading">
              <div className="summary-heading-row"><div><span className="section-kicker">Run record · Ticket {selectedSnapshot.result.ticket_id}</span><h3 id="summary-heading">What the team did</h3></div><span className={`status-stamp status-stamp--${String(selectedSnapshot.result.saved_ticket.status ?? "unknown").replaceAll("_", "-")}`}>{labelForStatus(selectedSnapshot.result.saved_ticket.status ?? "unknown")}</span></div>
              <div className="summary-list">
                {AGENTS.map((agent) => {
                  const lines = summaryLines(agent, selectedSnapshot.events);
                  return <div className={`summary-row agent-tone--${AGENT_META[agent].color}`} key={agent}><strong>{agent}</strong><p>{lines?.length ? lines.join(" ") : "Not called on this ticket; no agent output was recorded."}</p></div>;
                })}
              </div>
            </section>
          )}
        </section>

        <aside className="right-rail" aria-label="Cash register and approvals">
          <section className="cash-register panel" aria-labelledby="cash-heading">
            <div className="cash-top"><div><span className="section-kicker">03 · Till</span><h2 id="cash-heading">Cash register</h2></div><span className="register-star" aria-hidden="true">✳</span></div>
            <div className={`cash-window ${approvedFlashId !== null ? "cash-window--flash" : ""}`}>
              <span className="cash-label">CHECKING · USD</span>
              <strong className="cash-balance">{displayBalance === null ? "— — —" : money(displayBalance)}</strong>
              <span className="cash-meta">Current balance · live from database</span>
            </div>
            <div className="cash-ledger">
              <div><span>Held for pending approvals</span><strong>{checking ? money(checking.pending_obligations) : "—"}</strong></div>
              <div><span>Available before new request</span><strong>{checking ? money(checking.available_balance) : "—"}</strong></div>
              <div><span>Balance date</span><strong>{checking ? prettyDate(checking.date) : "—"}</strong></div>
              <div><span>Shop today</span><strong>{shopToday ? prettyDate(shopToday.date_today) : "—"}</strong></div>
            </div>
            {shopToday?.notes && <p className="desk-note">{shopToday.notes}</p>}
          </section>

          <section className="approvals-panel panel" aria-labelledby="approvals-heading">
            <div className="section-heading approval-heading">
              <div><span className="section-kicker">04 · Human decision</span><h2 id="approvals-heading">Approvals tray</h2></div>
              <span className="count-badge count-badge--alert">{approvals.length}</span>
            </div>
            <p className="panel-intro">Agents prepare the request. You decide what leaves the till.</p>
            <label className="approver-field">Approver name / initials<input value={approver} onChange={(event) => setApprover(event.target.value)} placeholder="Required for the audit record" autoComplete="name" /></label>
            <div className="approval-stack">
              {approvals.map((approval) => <ApprovalSlip
                key={approval.id}
                approval={approval}
                approver={approver}
                busy={busyApprovalId === approval.id}
                flash={approvedFlashId === approval.id}
                error={approvalErrors[approval.id]}
                onApprove={() => void approve(approval)}
                onReject={() => void reject(approval)}
              />)}
              {!initialLoading && approvals.length === 0 && <div className="empty-tray"><span className="empty-tray-icon" aria-hidden="true">✓</span><strong>Nothing waiting for approval.</strong><span>New payment or purchase requests will land here.</span></div>}
              {initialLoading && approvals.length === 0 && <div className="empty-tray">Checking the approvals tray…</div>}
            </div>
          </section>
        </aside>
      </main>

      <footer className="activity-footer" aria-labelledby="activity-heading">
        <div className="activity-title"><span className="section-kicker">05 · Shop-floor log</span><h2 id="activity-heading">Activity tape</h2>{activeRunId !== null && <span className="tape-live"><i />LIVE</span>}</div>
        {activityError && <p className="activity-error" role="alert">{activityError}</p>}
        <div className="activity-tape" aria-live="polite" aria-label="Recent agent and tool events" tabIndex={0}>
          {activityEvents.length === 0 ? <div className="tape-empty">{initialLoading ? "Loading the audit tape…" : "No agent events yet. Activity will appear here as the team works."}</div> : activityEvents.slice().reverse().map((event) => (
            <article className={`activity-ticket ${event.step.includes("error") ? "activity-ticket--error" : ""}`} key={event.id}>
              <div className="activity-ticket__top"><span className="activity-agent">{event.agent}</span><span className="activity-time">{new Date(event.time).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</span></div>
              <strong>{event.tool_name || event.step.replaceAll("_", " ")}</strong>
              <p>{resultText(event)}</p>
              {event.step === "mcp_tool_started" && <span className="tool-tag activity-tool">{event.tool_name}</span>}
              {eventTicketId(event) !== null && <span className="activity-ticket-id">TICKET {eventTicketId(event)}</span>}
            </article>
          ))}
        </div>
        <div className="footer-note"><span>Campus Customs · human-in-the-loop operations</span><span>{activityEvents.length} recent recorded event{activityEvents.length === 1 ? "" : "s"} · audit log remains append-only</span></div>
      </footer>
    </div>
  );
}
