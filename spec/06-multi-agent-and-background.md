# 06 — Multi-agent and background work (Claude Code, public docs 2026-10)

## Overview
Claude Code layers several delegation primitives of increasing scale:
**subagents** (typed workers with their own context, returning a summary),
**forks** (subagents that inherit the parent conversation and cache),
**background tasks** (shells, monitors, background subagents),
**agent teams** (experimental lead + peer sessions with a shared task list and
mailboxes), **cross-session messaging**, **dynamic workflows** (a JavaScript
script that orchestrates up to hundreds of subagents), and **scheduled/remote
agents** (`/loop`, cron tools, routines). Each moves more of the plan and the
intermediate state out of the main context window.

## Diagram
```
main session ──Agent(type, prompt)──► subagent (own ctx, own tools/model) ──► summary
     │        ──Agent(fork)─────────► fork (inherits history + cache)
     │        ──run_in_background───► bg shell / Monitor / bg subagent ─► completion notice
     │        ──SendMessage(id|name)► resume subagent / teammate / other session
     │        ──Workflow(script)────► runtime: agent()/parallel()/pipeline()/phase()
     │                                 ≤16 concurrent, ≤1000 agents, resumable
     └─ team lead ◄─► teammates (mailbox JSON files, shared task list w/ deps, file locks)
 schedulers: /loop + ScheduleWakeup · CronCreate (session) · RemoteTrigger/routines (cloud)
```

## Mechanics

### Subagents
- Built-ins: **Explore** (fast read-only search; thoroughness levels; skips
  CLAUDE.md and git status), **Plan** (read-only research in plan mode),
  **general-purpose** (all tools), plus small utility agents. Explore/Plan are
  one-shot.
- Custom definitions: Markdown + YAML frontmatter in managed → `--agents`
  JSON → `.claude/agents/` → `~/.claude/agents/` → plugin `agents/`
  (highest first). Fields: `name`, `description`, `tools`/`disallowedTools`,
  `model` (alias/ID/`inherit`), `permissionMode`, `maxTurns`, `effort`,
  `skills`, `mcpServers`, `hooks`, `memory` (user/project/local),
  `background`, `omitClaudeMd`, `isolation: worktree`, `initialPrompt`.
- Hot-reloaded within seconds.
- Only the final report returns to the caller; reports are scanned for
  suspicious text and may be flagged (never truncated) before the parent reads them.
- Limits: nesting depth 3 by default (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`),
  20 concurrent (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`).
- Foreground blocks the turn; background runs concurrently with a reduced
  built-in toolset, permission prompts surface in the main session; Ctrl+B
  backgrounds a running task. Results arrive as notifications.
- Resume: completed subagents keep an ID; `SendMessage` to it continues with
  full history. Transcripts retained 30 days.
- `isolation: worktree`: temporary git worktree branched from default
  branch; removed automatically if unchanged; commands resolving to the main
  checkout fail.
- Permission rules: `Agent(Name)` deny/allow.
- Subagents get a separate cache TTL bucket (5 min default).
- (Observed in use) In auto mode, background subagents hand their final
  report back through a dedicated hand-off tool rather than plain text.

### Background tasks
- Bash `run_in_background`, auto-backgrounding on timeout, `Monitor` event
  streams, background subagents; output in files; `TaskStop`; reminders on
  completion; still listed after compaction to avoid duplicates.
- Background sessions ("agent view"): whole sessions run detached; attach,
  stop, list via `claude agents`.

### Agent teams (experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`)
- Lead session spawns named teammates (full sessions, own context); in-process
  or split panes (tmux/iTerm2).
- Mailboxes: per-agent JSON files under `~/.claude/teams/<team>/inboxes/`;
  shared task list with pending/in-progress/completed and dependencies under
  `~/.claude/tasks/<team>/`; claiming uses file locks.
- Hooks: `TeammateIdle`, `TaskCreated`, `TaskCompleted` (exit 2 = keep
  working / reject).
- Inter-agent messages are labelled as coming from another Claude session; a
  teammate cannot grant approvals on the user's behalf; in auto mode the
  classifier reviews each message and treats relayed approvals as untrusted.
- Limits: one team per session, no nested teams, lead fixed, no resume of
  in-process teammates.

### Dynamic workflows
- Claude writes a JavaScript script (`meta` + body with top-level await);
  primitives `agent(prompt, {schema, label, stallMs})`, `parallel()`,
  `pipeline()`, `phase()`, `log()`, `args`.
- Runs in an isolated runtime with no fs/shell/imports of its own; agents do
  the work. Determinism enforced (`Date.now`, `Math.random` throw) so runs can
  be replayed: completed agents return cached results until the first
  divergent prompt.
- Limits: 16 concurrent (CPU-bounded), 4,096 items per fan-out, 1,000 agents
  per run; stalled agents restart up to 5 times; usage-limit waits pause the
  run. Structured output validated (5 retries).
- Approval prompt shows planned phases; can be saved to
  `.claude/workflows/` or `~/.claude/workflows/` and run as `/<name>`;
  distributable in plugins. Bundled `/deep-research`.
- Same-prefix fan-out agents are staggered (≤5 s) to share cache.
- Prompts computed by a script are not treated as user intent by the auto
  classifier.

### Scheduling
- `/loop [interval] <prompt>` (self-paced via `ScheduleWakeup`), session cron
  tools (`CronCreate/Delete/List`), desktop scheduled tasks (machine must be
  awake), cloud **routines** (`/schedule`, `RemoteTrigger`), `PushNotification`
  to reach the user.

## Harness design takeaways
- **T1** — Offer a ladder of delegation primitives; choose by *who holds the
  plan* (model turn-by-turn vs. script) and *where intermediate state lives*.
- **T2** — Return summaries, not transcripts, from workers; scan worker
  reports for injection before the parent reads them.
- **T3** — Forks that share the parent prefix are nearly free on cache;
  fresh subagents are cheap on context but pay a new prefix.
- **T4** — Bound recursion and concurrency with explicit, tunable limits.
- **T5** — Make orchestration scripts deterministic so they can be replayed
  and resumed from cached agent results.
- **T6** — Inter-agent messages must carry provenance and can never stand in
  for human approval.
- **T7** — Worktree isolation per worker avoids edit conflicts and makes
  cleanup automatic when nothing changed.
- **T8** — Plain files (JSON mailboxes, task dirs, file locks) are an adequate
  and debuggable substrate for local multi-agent coordination.

## Open questions
- How background-subagent tool sets are reduced exactly. (uncertain)
- Server-side vs. local execution of routines and how they authenticate to
  repos.
