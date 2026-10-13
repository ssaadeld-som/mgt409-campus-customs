# The Back Room — dashboard design

Campus Customs’ dashboard is a working print-shop desk, not a dark SaaS console. The palette is warm paper, kraft, near-black ink, and small risograph accents (brick, green, blue, violet, and ochre). Serif/slab headings, clean sans-serif body text, and monospaced IDs and money keep the board characterful but easy to scan. Paper grain, order-slip rules, and approval stamps are restrained; animations are brief, purposeful, and respect reduced-motion settings.

The laptop layout has three working zones plus an activity footer: the left ticket rail, a larger center desk, and a right cash register / approvals tray. It collapses to a single column on smaller screens. Ticket slips load only from FastAPI and show database ID, subject, requester, and live status; selecting one focuses the desk, and Run agents is disabled unless that ticket is open or another run is active.

Five always-visible stations—Boss, Inventory, Accounting, Facilities, and Customer Service—use distinct accent colors. Station state, latest statement, and tool tags are derived from recorded agent events. Live runs poll the audit endpoint about once a second; delegation questions appear on a short animated handoff line. Completed-run summaries use only agent completion events, one or two lines per role; an uncalled agent is labeled as not called rather than given invented work.

The approvals tray shows recorded payee/vendor, amount, reason, and requesting agent, with explicit human-name confirmation for Approve and Reject. Backend refusals remain visible on the slip. Approval refreshes the live checking balance and gives a brief stamp/number transition; the register also shows pending commitments, balance date, and the shop date from `desk.date_today`. Reset asks for confirmation and refreshes every panel while preserving the append-only audit tape.

Controls are keyboard reachable with visible focus, readable contrast, loading and empty states, and friendly API errors instead of blank panels. The UI contains no ticket, balance, product, or agent-output fixtures: operational values come from the FastAPI routes.
