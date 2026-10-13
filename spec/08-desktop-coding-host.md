# 08 — Desktop coding host (Claude desktop app, Code tab; public docs 2026-10)

## Overview
The desktop app's Code tab is a multi-session GUI host around the Claude Code
engine. It adds things a terminal cannot: parallel sessions in a sidebar,
per-session git worktrees, a file-by-file diff pane with line comments, an
embedded browser pane with dev-server launch configs and auto-verification,
an integrated terminal and file editor, PR status with CI auto-fix and
auto-merge, scheduled tasks, connectors, side chats, notifications, and
computer use. Environments can be local, cloud, SSH, or WSL. The host also
exposes host-side tools to the agent (browser control, session management,
editor/reveal, PR monitoring).

## Diagram
```
┌ sidebar ─────────┐ ┌ session ──────────────────────────────────────────────┐
│ sessions (groups,│ │ chat pane │ diff (+N −M, line comments → Cmd+Enter)   │
│ pinned, unread)  │ │           │ browser pane (launch.json, autoVerify)    │
│ env: local/cloud │ │ plan pane │ terminal (Ctrl+`) │ file editor │ tasks   │
│ /SSH/WSL         │ │ subagents │ PR status: CI, auto-fix, auto-merge       │
└──────────────────┘ └───────────────────────────────────────────────────────┘
        │ worktree per session: <repo>/.claude/worktrees/<name> (+ .worktreeinclude)
        ▼
   Claude Code engine (local process | SSH remote helper | cloud VM)
```

## Mechanics

### Sessions and worktrees
- Each session: own history, folder, environment, permission mode, changes.
- Optional isolated git worktree per session (default under
  `<project>/.claude/worktrees/`, configurable location and branch prefix);
  `.worktreeinclude` copies selected gitignored files (e.g. `.env`) into new
  worktrees; auto-archive after PR merge/close.
- Side chats (`/btw`, Cmd+;) ask questions with session context without
  polluting the main thread; not persisted.
- Cross-session tools: the agent can list, read, message, rename, and archive
  other local/SSH/WSL desktop sessions (20 most recent by default; asks before
  archiving); `crossSessionInbound: "refuse"` blocks inbound messages.

### Permission modes in desktop
Manual, Accept edits, Plan, Auto, Bypass (same semantics as `04`). Selected
mode is remembered per folder except Plan. Cloud sessions: Accept edits /
Plan / Auto only. Bypass off on Team/Enterprise unless policy allows.

### Browser pane and dev servers
- Tabbed browser with a clean profile (separate from the user's browser).
- `.claude/launch.json`: `configurations[]` with `name`,
  `runtimeExecutable`, `runtimeArgs`, `port`, `cwd`, `env`, `autoPort`,
  `program`, `args`, `url`; top-level `autoVerify` (on by default) has Claude
  start servers and verify changes with screenshots/DOM inspection.
- External sites need per-site approval (once / always / deny, subdomains
  included); localhost and project files need none; safety classifiers review
  write actions in every mode. Admin keys: `browserExternalPageTools`,
  `disableBrowserExternalNavigation`.
- (Observed in use) Browser tools offer accessibility-tree reads with element
  refs, find, form input, JS evaluation for inspection, console/network logs,
  screenshots/zoom, viewport emulation, and batched actions.

### Review and PRs
- Diff view: per-file, click a line to comment, submit comments as one
  message to the agent.
- `/code-review` produces a review card with "walk through in diff" and
  "apply fixes".
- PR status via `gh`: CI auto-fix (reads failures, iterates; also addresses
  review comments from trusted authors locally), auto-merge (squash once
  checks pass).

### Other host features
- Terminal (local/SSH), file editor with external-change warnings, context
  menu (attach, open in editor, reveal, copy path).
- Connectors, skills, plugins via the `+` menu (not in cloud/WSL).
- OS notifications when a session finishes unobserved.
- Scheduled tasks (machine must stay awake); Remote Control per session or by
  default; Dispatch from phone spawns Code sessions (Pro/Max).
- iOS Simulator pane for testing iOS apps (docs page exists; details not
  covered here).
- Admin: `disableDesktopLocalSessions`, `sshConfigs`, `sshHostAllowlist`,
  console toggles for Desktop / Cloud sessions / Remote Control.

## Harness design takeaways
- **T1** — One worktree per session is the simplest robust answer to
  parallel agents in one repo; automate creation, include-lists, and cleanup.
- **T2** — Diff-anchored line comments batched into one agent message make
  human review a first-class input channel.
- **T3** — A host-owned browser with a clean profile plus declarative dev
  server configs lets the agent verify UI work without touching the user's
  real browser state.
- **T4** — Treat localhost as trusted and external navigation as
  permissioned, with admin kill switches.
- **T5** — Close the loop on PRs (CI failure → fix → re-push → auto-merge)
  inside the host, gated by repo settings.
- **T6** — Host-provided tools (session mgmt, editor, preview, notifications)
  are just more tools to the agent, with their own permission semantics.
- **T7** — Side chats give "ask without derailing" cheaply.

## Open questions
- Internal protocol between the desktop host and the engine for host tools
  (likely MCP-shaped from the agent's view). (uncertain)
- Exact persistence model for SSH remote helpers beyond documented limits
  (30-day helper idle stop).
