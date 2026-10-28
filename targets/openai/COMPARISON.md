# OpenAI Codex desktop vs Claude Code — harness comparison (D1–D15)

Compares the OpenAI ChatGPT/Codex desktop app 26.930 with bundled Codex CLI
0.160 (`targets/openai/spec/`, cited as **NN/Tk**) against the Claude Code
baseline (`spec/`, cited as **spec/NN/Tk**), scored with `audit/RUBRIC.md`.

**Read the evidence asymmetry first.**
- The OpenAI spec comes from *static analysis* of one shipped build. It is
  deep (mechanisms, limits, internal protocols), but nothing was executed, so
  runtime behavior, server-side decisions and remotely refreshed catalogs are
  inferred. Features behind experimental gates may not be live for users.
- The Claude Code spec comes from *public documentation* (2026-10) and
  first-hand use. It describes intent and user-visible behavior, rarely
  internals. Absence of an internal mechanism there means "not documented",
  not "not present".
- The result is that OpenAI tends to look richer in mechanism detail, and Claude Code
  tends to look cleaner in policy statements. Scores try to correct for this;
  where they cannot, the justification says so.

Scale per rubric: **0** absent · **1** basic · **2** solid, configurable ·
**3** best-in-class, principled, composable. `?` = unknown.

---

## D1 — Agent loop & streaming

**OpenAI approach**
- Headless core with a submission queue and a typed event stream, fronted by a
  versioned JSON-RPC server; desktop, TUI, IDE and remote are all clients
  (01/T1, 00/T1).
- Turn is a named phase sequence with a trace span each; loop continues if
  the model wants more or the user has queued input (01/T2).
- Three distinct controls: steer into the running turn, queue for the next
  turn, interrupt; steer is refused with a typed error where unsafe (01/T3).
  Interrupt also reaches the transport as an explicit cancel frame (01/T6).
- Layered retries with transport fallback (WebSocket → SSE), non-fatal
  "reconnecting" signals, and non-idempotent turn RPCs reconciled by client
  message id (01/T7, 01/T8, 01/T9).

**Claude Code approach**
- Classic streamed tool-use loop over the Messages API, framed as gather /
  act / verify (spec/01 Overview).
- Steering via queued messages read between tool calls (spec/01/T2); hooks
  can veto "stop" to build goal-seeking loops (spec/01/T3).
- Tool calls are recorded before execution so crashes resume safely
  (spec/01/T4); one engine drives every surface and the Agent SDK exposes its
  event stream (spec/00/T1, spec/00/T3).

**Scores** — OpenAI **3**: steer/queue/interrupt, transport-level cancel,
typed protocol for other hosts, idempotency handling. Claude Code **3**:
streaming, queued steering, crash-safe tool records, SDK event stream.

**Verdict** — Both best-in-class. OpenAI's loop is more explicitly engineered
as a networked service (idempotency, reconnect semantics, cancel frames);
Claude Code's is simpler and leans on hooks for loop control. Partly an
evidence artefact: Claude Code's internals are not documented.

**Pattern to copy** — OpenAI → us: treat any turn-creating call as
non-idempotent; attach a client message id, keep an un-acked list, and
reconcile with server state after a disconnect (01/T9). Also adopt the
post-interrupt developer note telling the model that background work may
still be running and to verify state before repeating actions (01/T4).

---

## D2 — Model/API integration & reasoning controls

**OpenAI approach**
- A server-hosted, locally cached model catalog decides per-model base
  instructions, effort ladder, verbosity, tool variants and service tiers
  (01/T12, 03/T9).
- Effort ladder from none/minimal up to xhigh; per-role model and effort
  overrides for sub-agents (01 Mechanics, 06/T6).
- Persistent WebSocket with incremental continuation (prior response id plus
  new items), with a local prefix check before relying on it (01/T5).
- Model reroutes and fallbacks surface as their own events (01/T13);
  per-provider retry config in `[model_providers.*]` (01/T7).

**Claude Code approach**
- Request layout ordered by volatility; every mid-session change is an
  append, making caching an invariant (spec/01/T1, spec/02/T5).
- Model aliases incl. a plan-vs-execute split, effort levels and extended
  thinking, 1M-context variants, safety-triggered fallback model (spec/01
  Mechanics).
- Several providers (Anthropic API, Bedrock, Vertex, Foundry, gateways) and
  per-request-class cache TTL policy (spec/00 Diagram, spec/01/T6); warns
  before cache-busting actions (spec/01/T7).

**Scores** — OpenAI **3**: catalog-driven per-model behavior, effort ladder,
per-role routing, cache-affine continuation. Claude Code **3**: multi-provider,
plan/execute routing, cache-aware layout as an architectural rule.

**Verdict** — Different trade-offs. OpenAI moves model behavior into remotely
updatable data (fast iteration, less client churn, but harder to audit what a
given session ran with). Claude Code is more principled about cache economics
and more open to third-party clouds.

**Pattern to copy** — OpenAI → us: a per-model capability catalog (prompt
snippets, tool flavours, effort ladder, output caps) cached with an identity
check and ETag refresh, so model-specific behavior is data rather than code
branches (01/T12, 03/T9). Claude Code → OpenAI: explicit pre-action warnings
when a user action will invalidate a warm cache (spec/01/T7).

---

## D3 — System prompt assembly & instruction files

**OpenAI approach**
- Prompts ship as per-model catalog data with feature snippets (approvals,
  plan mode, multi-agent roles) (02/T1).
- Developer context is a set of named, tagged sections, diffed per step and
  snapshotted into the rollout; changes are superseding fragments, never edits
  (02/T2, 02/T3, 02/T4).
- AGENTS.md layered from global down each directory to cwd, override file,
  fallback names, shared 32 KiB budget with visible truncation (02/T5).
- Permissions section generated from the active policy enums (02/T8); trust
  levels of each source stated in the prompt (02/T6).

**Claude Code approach**
- Hierarchical CLAUDE.md from org → user → ancestors → cwd → local, with
  AGENTS.md honoured as fallback (spec/02/T1, spec/02/T3).
- Lazy path-triggered loading of nested files and `paths:` rules for
  monorepos (spec/02/T2); maintainer comments stripped (spec/02/T6).
- Separate agent-written auto memory, capped at load (spec/02/T4); dynamic
  context arrives as appended reminders (spec/02/T5); instructions declared
  advisory (spec/02/T7).

**Scores** — OpenAI **3**: layered files, conditional per-feature sections,
snapshot persistence makes the effective prompt reproducible. Claude Code
**3**: scoped/nested discovery, rules, imports, append-only reminders.

**Verdict** — Even. OpenAI is stronger on *reproducibility* (snapshotted
sections let resume/fork see exactly what the model saw); Claude Code is
stronger on *scoping* (lazy nested and path rules below cwd, org lane).

**Pattern to copy** — OpenAI → us: model dynamic context as named sections
with stable ids, persist a snapshot per step, and supersede rather than
rewrite (02/T2, 02/T3). Claude Code → OpenAI: path-triggered rules so
subdirectory conventions load only when touched (spec/02/T2).

---

## D4 — Tool design & schemas

**OpenAI approach**
- "Code mode" for newer models: one freeform `exec` tool running JavaScript
  in a fresh V8 isolate, with every other tool as an async global; direct
  function calling as fallback (03/T1, 03/T3).
- Tool schemas rendered as TypeScript declarations with a shared preamble
  (03/T2); schema sanitation and a metadata byte budget for MCP (03/T11).
- Deferred tools grouped into namespaces and found with BM25 `tool_search`
  (03/T7); small, hard-validated UX tools (plan, question, context query)
  (03/T12).
- Validation errors phrased as corrective instructions (03/T8).

**Claude Code approach**
- Mid-sized set of purpose-built JSON-schema tools; one canonical name string
  reused by permissions, hooks and agent configs (spec/03/T1).
- Rare and MCP tools deferred behind `ToolSearch` (spec/03/T5).
- Web fetch summarizes through a prompt to bound context and injection
  surface (spec/03/T7); event-streaming monitor tool (spec/03/T8).

**Scores** — OpenAI **3**: deferred loading, schema hygiene, code-mode
composition. Claude Code **3**: minimal orthogonal set, deferred loading,
names that compose with policy.

**Verdict** — OpenAI is more ambitious. Code mode lets the model batch,
parallelize and filter tool output without spending turns. The cost is a new
execution surface that must itself be sandboxed and audited. Claude Code's
surface is easier to reason about and to write policy against. Code mode is
gated per model in the catalog, so how widely it is enabled is inferred.

**Pattern to copy** — OpenAI → us: offer a scripting tool that can call other
tools in-process (with capability handles, not raw fds, per 09/T2) for
read-heavy fan-out, while keeping mutations and approvals sequential (03/T1,
03/T14). Present schemas as TypeScript signatures (03/T2).

---

## D5 — Shell & file editing tools

**OpenAI approach**
- `apply_patch` as a freeform tool constrained by a grammar; heredoc
  interception so shell-style patching also works (03/T6).
- PTY shell sessions with yield/resume: return a handle after a yield window,
  poll for new output later (03/T4, 06/T9).
- Single default output cap (10000 tokens) per call with override, middle
  truncation and metadata (original count, lines, session id) (03/T5, 05/T6).
- Each shell call can carry its own escalation request (03/T8).

**Claude Code approach**
- Exact-string edits with uniqueness and read-before-edit guards; stale-file
  edits allowed only when the match proves safety (spec/03/T2).
- Persistent shell cwd; slow foreground commands auto-backgrounded instead of
  killed (spec/03/T3).
- Large outputs spilled to a file with a preview the model can grep
  (spec/03/T4); shell reads treated as reads for guards (spec/03/T6).

**Scores** — OpenAI **2**: patch edits, background PTYs, structured
truncation; no explicit read-before-edit or stale-file guard is described (patch
context lines give only implicit protection; may be a static-analysis gap).
Claude Code **3**: all rubric-3 items documented.

**Verdict** — Claude Code is stronger on edit safety (read-before-edit,
staleness) and on not losing output (spill to file vs. middle truncation).
OpenAI is stronger on long-running interactive processes (poll by handle,
bounded yields).

**Pattern to copy** — Both directions. OpenAI → us: yield/poll handles for
PTY sessions with explicit yield bounds (03/T4). Claude Code → OpenAI:
spill truncated middles to a file the model can grep rather than discarding
them (spec/03/T4).

---

## D6 — Permissions & approvals

**OpenAI approach**
- Three orthogonal axes (OS sandbox profile, Starlark exec-policy rules,
  approval reviewer = human or LLM); UI modes are named combinations (04/T1).
- Rules with inline match/no-match tests, executables pinned to absolute
  paths, per-segment evaluation, fail closed on shell complexity (04/T4,
  04/T5).
- One-click approval becomes a durable prefix or domain rule (04/T6); the LLM
  approver returns strict JSON risk × authorization with tuned false-positive
  guidance (04/T10, 04/T11).
- Managed requirements constrain the allowed values on each axis, from MDM,
  cloud and project layers (04 Mechanics, 04/T13).

**Claude Code approach**
- Modes plus allow/ask/deny rules in one grammar across tools, deny-first and
  scope-mergeable (spec/04/T1, spec/04/T2).
- Auto-mode classifier sees intent and actions, not tool results, bounded by
  block counters (spec/04/T3, spec/04/T5); allowlist narrowed on entering auto
  (spec/04/T4).
- Hard circuit breakers no rule can override; workspace trust gates
  project-supplied permissions (spec/04/T6, spec/04/T8); managed settings
  above all (spec/10/T4).

**Scores** — OpenAI **3**: modes, testable rules, LLM reviewer, managed
per-axis constraints. Claude Code **3**: modes, unified rules, classifier,
circuit breakers, managed policy.

**Verdict** — Even, different shapes. OpenAI's orthogonal-axes model makes
policy composition and enterprise constraints cleaner. Its rule language is
also more expressive and is self-testing. Claude Code's classifier design (blind to
tool output) and non-overridable circuit breakers are the stronger injection
story.

**Pattern to copy** — OpenAI → us: inline positive/negative examples in
permission rules, validated at load time (04/T4); and the reviewer prompt's
"a timeout is not a denial; once denied, do not route around it" contract
(04/T10). Claude Code → OpenAI: keep the auto-approver blind to tool results
(spec/04/T3).

---

## D7 — Sandboxing & isolation

**OpenAI approach**
- Per-command OS profile built from deny-all (Seatbelt / landlock+seccomp /
  restricted token), protected ancestors, symlinked roots refused (04/T7).
- All egress through a local proxy with domain allowlists, method limits via
  TLS interception, private-address blocking, credential injection (04/T8).
- Exec interception inside the shell so policy applies to sub-processes
  (04/T9); fail closed when enforcement is unavailable (04/T14).
- Model can request narrow extra paths or network while staying sandboxed
  (04/T3); worktrees, remote and cloud environments (08/T5, 08/T1).

**Claude Code approach**
- OS sandbox for shell with filesystem write limits and a filtering egress
  proxy; sandboxed commands run without prompts (spec/04/T7).
- Harness config protected inside the sandbox (spec/04/T7); escape hatch
  through approval.
- Worktree per session and cloud/SSH environments (spec/08/T1, spec/00/T2).

**Scores** — OpenAI **3**: configurable fs+net, per-call escalation, cloud
isolation, credential injection. Claude Code **3**: configurable fs+net,
approval escape hatch, cloud/remote isolation.

**Verdict** — OpenAI stronger in depth: per-process interception,
incremental grants and secret injection at egress go beyond what the Claude
Code docs describe. Caveat: Claude Code's sandbox internals are not public.

**Pattern to copy** — OpenAI → us: inject credentials at the egress proxy
so secrets never enter the sandbox (04/T8), and let the model request a
*narrow* grant rather than a full unsandboxed run (04/T3).

---

## D8 — Context management & compaction

**OpenAI approach**
- Two compaction backends behind one interface: provider-native opaque
  compaction with a local summarizer fallback; replacement history written to
  the rollout (05/T2).
- Separate pre-sampling and post-turn triggers with a scope switch so a large
  cached prefix does not cause early compaction (05/T3).
- Experimental model-managed windows: notes store, history by item id,
  start-new-window tool, reminder at 6144 tokens remaining (05/T5, 03/T13).
- Catalog budgets with "entries were dropped, look them up" notices (02/T12).

**Claude Code approach**
- Re-hydrating compaction: summarize, then deterministically reload
  instructions, memory, plan and hot files (spec/05/T1).
- Summarizer runs on the shared prefix so compaction is mostly a cache read
  (spec/05/T2); partial, user-anchored summarization (spec/05/T3).
- Spill-to-file, old tool-output clearing, sub-agent offload (spec/05
  Overview); thrash cap (spec/01/T5).

**Scores** — OpenAI **3**: auto-compaction, cache-aware triggers, sub-agent
offload, model-managed windows. Claude Code **3**: re-hydration,
cache-preserving summarizer, user-steerable partial summaries.

**Verdict** — Different trade-offs. Claude Code gives the *user* more
control (anchored partial summaries). OpenAI gives the *model* more control
(self-managed windows) and has a provider-native backend. The model-managed
scheme is experimental and statically observed only.

**Pattern to copy** — OpenAI → us: separate the compaction trigger for the
cacheable static prefix from the conversational body (05/T3). Claude Code →
OpenAI: re-hydrate durable context from disk after compaction rather than
trusting the summary to carry it (spec/05/T1).

---

## D9 — Session persistence / resume / fork

**OpenAI approach**
- Append-only rollout JSONL as source of truth, SQLite as a rebuildable
  projection with byte offsets for paging (05/T1).
- Resume, fork (by ordinal pointer, not copy), revert (conversation only),
  archive; per-thread writer locks (05/T8, 05/T7).
- Desktop per-turn snapshots in private git refs and reverse-patch undo
  (08/T3, 08/T4); cross-host handoff as transcript + git bundle (08/T7).

**Claude Code approach**
- Append-only JSONL per session enabling resume, fork, export (spec/05/T4).
- Checkpoints rewind code, conversation or both; scope limited to
  harness-written files (spec/05/T6).
- Branch-in-process vs fork-new-process distinguishes permission state
  (spec/05/T5); resume never silently restores a permissive mode
  (spec/05/T7); cloud/remote handoff (spec/00/T2).

**Scores** — OpenAI **3**: resume/fork, git-backed undo, cross-host handoff,
indexed history. Claude Code **3**: resume/fork, unified code+conversation
rewind, cloud handoff, export.

**Verdict** — Even. OpenAI's git-ref snapshots capture *all* worktree changes,
shell side effects included, without touching user state, which is stronger
than file-write checkpoints. Claude Code's single rewind of both code and
conversation is the cleaner UX, and so is its explicit handling of permissions on resume.

**Pattern to copy** — OpenAI → us: snapshot each turn as a tree hash via a
throwaway index into a private ref namespace, and undo by reverse 3-way apply
(08/T3, 08/T4).

---

## D10 — Multi-agent & background tasks

**OpenAI approach**
- Agent tree addressed by hierarchical path, persisted edges and status
  (06/T1); results arrive as mailbox messages, wait returns only status
  (06/T3).
- Context inheritance as none/all/last-N with ordinal-pointer forks (06/T4);
  built-in roles as config overlays (06/T6); depth, concurrency and shared
  token-budget limits (06/T7).
- Blackboard channels for larger teams (06/T8); PTY background sessions with
  an awaiter role (06/T9); cron/heartbeat automations (06/T10); cloud
  best-of-N (06 Mechanics).
- Harness internals (reviewer, memory consolidation) run as sub-agents
  (06/T12).

**Claude Code approach**
- Ladder of primitives: subagents, forks, background tasks, agent teams,
  cross-session messaging, scripted workflows, scheduled/remote agents
  (spec/06/T1).
- Summaries not transcripts, with injection scanning of reports
  (spec/06/T2); explicit recursion and concurrency limits (spec/06/T4).
- Deterministic, replayable orchestration scripts (spec/06/T5); worktree per
  worker (spec/06/T7); messages carry provenance and never stand in for
  approval (spec/06/T6).

**Scores** — OpenAI **3**: parallel tree, messaging, automations, cloud
attempts, worktrees. Claude Code **3**: fan-out up to scripted workflows,
messaging, scheduling, worktree isolation.

**Verdict** — Even. OpenAI has the more structured agent *addressing and
budgeting* model, with path ids and a tree-wide token budget. Claude Code has the
broader *orchestration* ladder, with replayable workflow scripts, and the clearer
provenance and approval rule for agent messages.

**Pattern to copy** — OpenAI → us: inheritance as none/all/last-N rather
than a boolean, and a weighted token budget shared across the whole tree
(06/T4, 06/T7). Separate "message" (no turn) from "task" (wakes a turn)
(06/T2).

---

## D11 — Extensibility

**OpenAI approach**
- MCP is the only tool substrate; skills, plugins, connectors and host tools
  all surface as MCP tools (07/T1).
- Per-server/per-tool policy matrix in config: approval mode, output limits,
  allow/deny, timeouts, parallel flag, context placement (07/T3, 07/T4).
- Skills catalog with lazy `SKILL.md`, explicit-only policy, declared
  dependencies with guided setup (07/T5, 07/T6); hooks can call MCP tools
  (07/T7); elicitation and MCP Apps widgets (07/T9, 07/T10).
- Imports Claude and Cursor manifests and instruction files (07/T8);
  hardened metadata reads (07/T14).

**Claude Code approach**
- Explicit split between advisory (CLAUDE.md, skills) and deterministic
  (hooks, rules) extension points (spec/07/T1).
- Progressive disclosure for skills and MCP (spec/07/T2); typed hook protocol
  with decisions, input rewriting and context injection (spec/07/T3); hooks
  cannot weaken deny rules (spec/07/T4).
- Plugins with namespacing and marketplaces under org policy (spec/07/T5);
  project scope cannot escalate trust (spec/07/T6); harness usable as MCP
  server and SDK (spec/07/T7).

**Scores** — OpenAI **3**: MCP, skills, plugins and marketplaces, hooks,
elicitation, rich UI. Claude Code **3**: same core set, with decision-controlling
hooks and trust-escalation guards.

**Verdict** — Even. OpenAI is broader (MCP Apps UI, elicitation widgets,
skill dependencies, rival-manifest import). Claude Code's hook protocol and
its anti-escalation rules are the more mature *policy* surface, and OpenAI's
hook event set is described as compatible with it.

**Pattern to copy** — OpenAI → us: let skills declare required MCP servers
and env vars so a missing dependency becomes a guided install (07/T6), and
give each MCP server an explicit context-placement setting (resident /
searchable / scripting-only) (07/T4).

---

## D12 — Desktop/IDE host integration

**OpenAI approach**
- `codex app-server` with versioned JSON-RPC and capability negotiation; the
  desktop is one client among IDE plugins and helpers (00/T1, 00/T3).
- Host id on every operation; local, WSL, SSH, remote-control and cloud
  backends behind one interface (08/T1, 08/T2); hardware-bound remote identity
  (08/T10).
- Diff panel with line comments, worktrees carrying uncommitted work,
  per-repo setup env reused in every shell, "open in" at `path:line:col`
  (08/T5, 08/T6, 08/T14).
- App-tools MCP lets the agent create/fork/hand off threads and update
  automations, gated by prompt (00/T7, 08/T8); PR review as an MCP App (08
  Mechanics).

**Claude Code approach**
- One engine across CLI, IDE extensions, desktop, web, mobile and CI
  (spec/00/T1); environment orthogonal to UI (spec/00/T2).
- Desktop: worktree per session, diff with batched line comments, preview
  browser with launch configs, terminal, PR status with CI auto-fix and
  auto-merge (spec/08/T1, spec/08/T2, spec/08/T3, spec/08/T5).
- Host-provided tools (session management, editor, preview, notifications)
  are ordinary tools with permission semantics (spec/08/T6); side chats
  (spec/08/T7).

**Scores** — OpenAI **3**: documented-by-code protocol for third-party
hosts, multi-host ids, host tools. Claude Code **3**: diff review, preview,
PR loop, host tools, split panes.

**Verdict** — Even. OpenAI's app-server protocol is the clearer third-party
host contract, and its multi-host model (WSL/SSH/remote/cloud by id) is more
uniform. Claude Code closes the PR loop (CI fix → auto-merge) more
completely in-host.

**Pattern to copy** — OpenAI → us: capture the per-repo setup script's
environment delta once and inject it into every terminal and agent shell in
that worktree (08/T6). Claude Code → OpenAI: in-host CI failure → fix →
re-push → auto-merge loop (spec/08/T5).

---

## D13 — Computer/browser use

**OpenAI approach**
- Computer Use as a separately signed helper app holding all OS permissions;
  the model reaches it only through a REPL object over typed RPC (09/T1,
  09/T2).
- Callers verified by a signing-team check on the socket peer's audit token,
  plus a separate context recording parent and OS-responsible process
  (09/T3, 09/T16, 09/T17); arguments frozen after policy checks (09/T7).
- Input addressed to the target window and process rather than the global
  event stream, so a background app is driven without taking focus; a
  cheap accessibility-only refresh path (09/T18, 09/T20).
- Category blocks (password managers, terminals, browsers, host apps) as
  one code-level predicate (09/T21), then managed
  allow/deny, then per-app prompts with scopes once/turn/session/always
  (09/T5, 09/T6); URL blocklist for browsers (09 Mechanics).
- Observe-then-act with accessibility-tree deltas, pixels only on request
  (09/T8); in-app browser panel with DevTools/WebMCP bridge, preferred over
  CU (08/T13).
- Opt-in Computer History on the same service: an event stream without
  screenshots, raw segments kept up to 48 hours, summarized into 10-minute
  and 6-hour memories, with a coarse-to-fine retrieval recipe and freshness
  check (09/T23, 09/T24, 09/T28).

**Claude Code approach**
- Preference ladder MCP → shell → browser → screen (spec/09/T1).
- Separate agent browser (clean profile) from the user's browser
  (spec/09/T2); per-site approval with accessibility refs and batching
  (spec/08 Mechanics).
- Per-app approval with category tiers, hidden harness UI, abort key,
  single-session screen lock, stop at logins/CAPTCHAs (spec/09/T3–T6).

**Scores** — OpenAI **3**: browser plus desktop control, per-app policy,
accessibility tree, scoped approvals. Claude Code **3**: both channels,
per-site/per-app permissions, a11y refs, batching. (Claude Code computer use
is a research preview.)

**Verdict** — OpenAI is stronger on *process security architecture* (a
separately signed helper, caller verification, TOCTOU freezing). Claude Code is
stronger on *explicit user-facing guardrails* (tool ladder, screen lock, abort
key, login/CAPTCHA stops). For OpenAI, caller verification and window-targeted
input are now confirmed at the native level; the policy and approval flow is
still largely inferred.

**Pattern to copy** — OpenAI → us: snapshot validated arguments into an
immutable object before acting (09/T7); deliver accessibility-tree changes as
deltas and pixels only on request (09/T8); attach per-app quirk notes on
first contact (09/T9).

---

## D14 — Safety & prompt-injection defenses

**OpenAI approach**
- Trust levels stated in the prompt: which sources may authorize actions,
  which are untrusted data (02/T6); app attachments split into user vs
  untrusted buckets (07/T11).
- Structured LLM approver with a no-route-around-denials rule (04/T10);
  fail-closed shell parsing and enforcement (04/T5, 04/T14).
- Taint tracking in memory: extraction skips threads polluted by external
  content, and activity memory stays descriptive and tainted (05/T11, 09/T15);
  Computer History capture policy ANDs app and URL rules, lets block win and
  always excludes private browsing, and settings writes need a fresh read
  plus confirmation before broadening (09/T25, 09/T26).
- Confirmation taxonomy for CU actions; third-party content never counts as
  consent (09/T14); managed requirements gate browser/CU and hooks (04
  Mechanics).

**Claude Code approach**
- Provenance labels on every non-user input, never carrying approval
  authority (spec/10/T1).
- Safety judge blind to tool output (spec/10/T2); scanning at boundaries,
  including worker reports (spec/10/T3).
- Org policy above all scopes with anti-widening locks (spec/10/T4);
  non-overridable circuit breakers (spec/04/T6); denied-actions retry queue
  (spec/10/T7).

**Scores** — OpenAI **3**: classifier review, provenance and taint, enforced
deny lists, admin policy. Claude Code **3**: classifier, provenance labels,
circuit breakers, admin policy.

**Verdict** — Even, with complementary strengths. OpenAI's taint
propagation into persistent memory is a notable defense against
*persistent* injection. Claude Code's "judge blind to tool output" and
boundary scanning are the clearer defenses against *in-session* injection. The OpenAI
evidence is static; how the reviewer and blocklists behave server-side is unknown.

**Pattern to copy** — OpenAI → us: never let content derived from untrusted
sources enter long-term memory unlabelled, and keep the taint through
summarization (05/T11, 09/T15). Claude Code → OpenAI: exclude tool results
from the approver's inputs (spec/04/T3).

---

## D15 — Observability & UX of agent work

**OpenAI approach**
- Canonical token record (input, cached, cache-write, output, reasoning)
  per request and cumulative, mirrored into OTel GenAI attributes (01/T10);
  rate-limit and credit state pushed to the UI (01/T11).
- Turn diffs, checkpoints/undo, line comments, commit and PR creation in the
  desktop (08 Mechanics); persistent notifications for approvals with inline
  replies (08/T11).
- Review mode with strict JSON findings prioritized P0–P3 and rule citations
  (02/T13); typed tool-call telemetry and sandbox-denial events (03/T15,
  04/T15); assistant messages tagged interim vs final (01/T15).

**Claude Code approach**
- Inline diffs, plan/task panes, `/context`, `/usage`, status-line JSON,
  notifications (spec/10/T5).
- OpenTelemetry metrics/events export, off by default and content-off, plus
  admin analytics (spec/10/T6).
- Recently-denied queue with one-key retry (spec/10/T7); review commands and
  a code-review app (spec/10 Mechanics); checkpoints (spec/05/T6).

**Scores** — OpenAI **3**: diffs, checkpoints, review workflow, quota UI,
OTel. Claude Code **3**: diffs, plan display, checkpoints, review workflows,
OTel export with privacy defaults.

**Verdict** — Even. OpenAI surfaces quota *before* rate limits bite and
types the interim/final distinction. Claude Code has the clearer
user-controlled telemetry posture (default-off, content-off) and in-session
context visibility. Whether OpenAI's OTel export is user-configurable could
not be determined statically.

**Pattern to copy** — OpenAI → us: parse rate-limit and credit headers on
every response and show remaining quota proactively (01/T11); tag each
assistant message as commentary vs final answer (01/T15).

---

## Summary scorecard

| Dim | Claude Code | OpenAI Codex desktop | Notes |
|---|---|---|---|
| D1 Agent loop & streaming | 3 | 3 | OpenAI richer on idempotency/reconnect; CC on hook-driven loops |
| D2 Model/API & reasoning | 3 | 3 | Catalog-as-data vs cache-as-invariant |
| D3 Prompt & instruction files | 3 | 3 | OpenAI reproducible sections; CC lazier scoping |
| D4 Tool design | 3 | 3 | Code mode is the boldest design in either harness |
| D5 Shell & file editing | 3 | 2 | No documented stale-file / read-before-edit guard in OpenAI |
| D6 Permissions & approvals | 3 | 3 | Orthogonal axes + testable rules vs blind classifier + breakers |
| D7 Sandboxing & isolation | 3 | 3 | OpenAI deeper (exec interception, egress credential injection) |
| D8 Context & compaction | 3 | 3 | User-steered vs model-managed |
| D9 Persistence / resume / fork | 3 | 3 | Git-ref snapshots vs unified rewind |
| D10 Multi-agent & background | 3 | 3 | Tree addressing/budgets vs workflow ladder |
| D11 Extensibility | 3 | 3 | OpenAI broader; CC stronger hook policy |
| D12 Desktop/IDE host | 3 | 3 | App-server protocol vs in-host PR loop |
| D13 Computer/browser use | 3 | 3 | Signed helper architecture vs explicit guardrails |
| D14 Safety & injection | 3 | 3 | Memory taint vs blind judge + boundary scans |
| D15 Observability & UX | 3 | 3 | Proactive quota vs privacy-default telemetry |
| **Total** | **45** | **44** | |

Both harnesses sit at or near the ceiling of the rubric, and the rubric no
longer separates them well. The verdicts and patterns above carry the real
differences. Neither score is the product of executed tests: OpenAI is scored from static
analysis of one build, Claude Code from public docs.

## Top 8 patterns worth adopting (ranked)

1. **Code-mode scripting tool with capability handles** (OpenAI 03/T1,
   03/T3, 09/T2) — one sandboxed JS tool that can call other tools lets the
   model batch and filter reads in code; give it capability handles, not raw
   fds or env, and keep mutations sequential.
2. **Structured, narrow escalation on the tool call** (OpenAI 04/T2, 04/T3)
   — sandbox level, user-facing justification and proposed reusable rule as
   fields, preferring incremental grants over unsandboxed runs.
3. **Egress proxy with credential injection** (OpenAI 04/T8) — secrets are
   added at the network boundary and never enter the sandboxed process.
4. **Git-ref turn snapshots + reverse-patch undo** (OpenAI 08/T3, 08/T4) —
   captures shell side effects too and never disturbs the user's index,
   stash or branches; complements file-level checkpoints.
5. **Approval judge blind to tool output** (Claude Code spec/04/T3,
   spec/10/T2) — the simplest strong firewall against injected content
   steering an automatic approver; worth adopting in any LLM reviewer.
6. **Supersede-not-edit named context sections, snapshotted per step**
   (OpenAI 02/T2, 02/T3) — makes resume/fork/compaction reproduce exactly
   what the model saw while keeping the cached prefix valid.
7. **Non-idempotent turn RPCs with client ids and reconciliation** (OpenAI
   01/T9, with 01/T4) — prevents duplicate turns after disconnects and tells
   the model to verify state after an interrupt.
8. **Re-hydrating compaction plus user-anchored partial summaries** (Claude
   Code spec/05/T1, spec/05/T3) — reload durable context from disk after
   summarizing, and let the user choose where summarization starts.

Close runners-up: taint-preserving memory (OpenAI 05/T11, 09/T15); testable
policy rules (OpenAI 04/T4); tree-wide token budget and none/all/last-N
inheritance (OpenAI 06/T4, 06/T7); spill-to-file output (Claude Code
spec/03/T4).
