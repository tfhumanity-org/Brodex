# OpenAI ChatGPT/Codex desktop harness — clean-room spec

Scope: the OpenAI ChatGPT/Codex desktop app for macOS, release 26.930, together
with the Codex CLI 0.160 bundled inside it (the CLI's `codex app-server` mode
runs the agent loop for the desktop). Written from static analysis of the
shipped artifacts under the `CLEANROOM.md` rules: paraphrase only, no internal
identifiers, no evidence pointers. Compared against the Claude Code baseline
in `spec/` using `audit/RUBRIC.md`; see [../COMPARISON.md](../COMPARISON.md).

Conventions:
- Same per-file template as `spec/` (Overview, Diagram, Mechanics,
  Takeaways T1..Tn, Open questions).
- Claims are marked **(observed)**, **(inferred)** or **(uncertain)** where it
  matters. Nothing was run, so runtime behavior (timing, server-side
  decisions, remotely refreshed catalogs) is inferred from code and config.
- Numbers (limits, timeouts, budgets) are as shipped in this build and may
  be changed by remotely delivered model catalogs or requirements.

| File | Topic | Rubric dims | Takeaways |
|---|---|---|---|
| [00-desktop-architecture.md](00-desktop-architecture.md) | Chromium-fork shell, React SPA, IPC broker, `codex app-server` over JSON-RPC, bundled toolchain | D12 | 14 |
| [01-agent-loop-and-api.md](01-agent-loop-and-api.md) | Submission queue + typed events, Responses API over WebSocket/SSE, steer/queue/interrupt, retries, token and rate-limit accounting, model catalog | D1, D2 | 15 |
| [02-system-prompt-and-context.md](02-system-prompt-and-context.md) | Per-model prompt catalog, named context sections with supersede-not-edit, AGENTS.md layering, trust levels, memory summary | D3 | 15 |
| [03-tools.md](03-tools.md) | Code mode (`exec` in a V8 isolate) vs direct tools, `apply_patch` grammar, PTY shell with yield/poll, deferred tools + `tool_search` | D4, D5 | 15 |
| [04-permissions-sandbox-approvals.md](04-permissions-sandbox-approvals.md) | Sandbox × exec-policy rules × approval reviewer, per-call escalation, egress proxy, LLM approver, managed requirements | D6, D7, D14 | 15 |
| [05-context-management-and-sessions.md](05-context-management-and-sessions.md) | Rollout JSONL + SQLite projection, two compaction backends, model-managed windows, goals, two-phase memory, worktree GC | D8, D9 | 14 |
| [06-multi-agent-and-background.md](06-multi-agent-and-background.md) | Path-addressed agent tree, mailbox results, roles, limits, blackboard, PTY sessions, automations, cloud best-of-N | D10 | 13 |
| [07-extensibility.md](07-extensibility.md) | MCP as sole tool substrate, skills, plugins + marketplaces, connectors, hooks, elicitation, MCP Apps | D11 | 15 |
| [08-desktop-coding-host.md](08-desktop-coding-host.md) | Host ids, private-ref turn snapshots and reverse-patch undo, worktrees, handoff, remote control, browser panel, notifications | D12, D15 | 14 |
| [09-computer-use.md](09-computer-use.md) | Separately signed helper app, REPL capability handles, signing-team peer check, window-targeted input, app policy tiers, observe-then-act deltas | D13, D14 | 22 |

Total: 152 takeaways.

## Top takeaways

The ten most transferable lessons, chosen for how much they would change
another harness's design rather than for novelty alone.

1. **01/T1** (with **00/T1**) — Build the loop as a headless core with a
   submission queue and typed event stream behind a versioned JSON-RPC
   server; every UI, including the desktop app, is just a client.
2. **03/T1** — Code mode: one freeform JavaScript tool that can call every
   other tool as an async function, so the model batches, parallelizes and
   filters results in code instead of spending turns and context.
3. **04/T1** — Treat OS sandbox, rules engine and "who answers approvals" as
   three orthogonal axes; UI modes are named combinations, and enterprise
   policy constrains each axis separately.
4. **04/T2** (with **04/T3**) — Escalation is a structured field on the tool
   call (sandbox level, user-facing justification, proposed reusable rule),
   preferring narrow incremental grants over leaving the sandbox.
5. **04/T8** — Route all network egress through a local proxy that enforces
   domain policy and injects credentials at the boundary, so secrets never
   enter the sandbox.
6. **02/T3** — Supersede rather than edit: changed context sections are
   re-emitted as fragments that void the earlier version, keeping history
   append-only and the cached prefix valid.
7. **05/T1** — Make the append-only rollout the source of truth and any
   database a rebuildable projection of it.
8. **01/T9** — Turn-creating RPCs are non-idempotent: stamp client message
   ids, track unacknowledged sends, and reconcile with the server after a
   disconnect instead of blindly retrying.
9. **08/T3** (with **08/T4**) — Snapshot turns into private git refs via a
   throwaway index, and undo by reverse-applying a patch, so per-turn diffs
   and undo never disturb the user's index, stash or branches.
10. **09/T1** (with **09/T2**) — Keep dangerous OS powers in a small,
    separately signed helper reached by typed RPC, and give model-written
    code capability handles rather than raw sockets or env.

Honourable mentions: **06/T3** (sub-agent results as mailbox messages),
**07/T1** (MCP as the single tool substrate), **04/T10** (structured LLM
approver), **05/T5** (model-managed context windows).

## Note on 10-safety-and-observability

There is no standalone OpenAI `10` file; the baseline's `spec/10` topics are
spread across the existing files:

- Prompt-injection and trust boundaries: `02` (trust levels stated in the
  prompt, T6), `07` (app content treated as untrusted, T11; metadata
  hardening, T14), `05` (memory extraction skips tainted threads, T11),
  `09` (confirmation taxonomy, T14; tainted activity memory, T15).
- Approvals, reviewer, managed policy: `04` (LLM approver, T10/T11; managed
  requirements in Mechanics; fail-closed enforcement, T14).
- Observability and review UX: `01` (token, rate-limit and OTel GenAI
  accounting, T10/T11; reroute events, T13), `03` (tool-call telemetry, T15),
  `02` (review prompt with prioritized JSON findings, T13), `08` (turn diffs,
  undo, notifications, T3/T4/T11), `04` (sandbox denial events, T15).
