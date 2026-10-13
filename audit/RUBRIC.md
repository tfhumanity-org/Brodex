# Harness comparison rubric (D1–D15)

Score each dimension 0–3 per harness. Score from the public spec
(`spec/`, `targets/<vendor>/spec/`), not from private notes. Record a one-line
justification and the spec file that supports it. Unknown ≠ 0: mark `?`.

Generic anchors: **0** absent · **1** basic/ad hoc · **2** solid, configurable
· **3** best-in-class, principled, composable.

| # | Dimension | Definition | 0 | 1 | 2 | 3 |
|---|---|---|---|---|---|---|
| D1 | Agent loop & streaming | How turns, tool calls, and model output are orchestrated and streamed. | Single-shot, no tool loop | Sequential tool loop, buffered output | Streaming, parallel tool calls, interrupt/steer mid-turn | Plus queued user input, robust retry/abort, typed event stream usable by other hosts |
| D2 | Model/API integration & reasoning controls | Provider/model selection, reasoning/effort knobs, caching, fallbacks. | Hard-coded model | Model switch only | Effort/thinking controls, prompt caching, multiple providers | Per-task model routing, budget-aware fallbacks, cache-aware prompt layout |
| D3 | System prompt assembly & instruction files | How the prompt is composed from base, env, project instructions, and user memory. | Static prompt | One project file | Layered files (user/project/local), env context, imports | Scoped/nested instruction discovery, conditional sections, inspectable final prompt |
| D4 | Tool design & schemas | Quality of the tool surface the model sees. | Shell only | Few coarse tools | Purpose-built tools with JSON schemas and clear descriptions | Minimal orthogonal set, deferred/lazy tool loading, consistent error contracts |
| D5 | Shell & file editing tools | Command execution and code modification mechanics. | No edits | Whole-file writes | Exact-match or patch edits, read-before-edit guard, persistent shell | Plus stale-file detection, background processes, structured output truncation |
| D6 | Permissions & approvals | What needs approval and how rules are expressed. | All-or-nothing | Per-call prompts only | Modes + allow/ask/deny rules with patterns, scoped by settings layer | Plus policy/managed settings, classifier-assisted auto mode, auditability |
| D7 | Sandboxing & isolation | OS-level containment of agent actions. | None | Working-dir convention | OS sandbox for shell (fs/network), worktrees/containers | Configurable fs+net policy, escape-hatch with approval, remote/cloud isolation |
| D8 | Context management & compaction | Keeping long sessions within the window. | Truncation | Manual clear | Auto-compaction/summarization, output truncation, file-state tracking | Plus cache-preserving compaction, sub-agent context offload, user-steerable summaries |
| D9 | Session persistence / resume / fork | Durability and branching of sessions. | Ephemeral | Transcript saved | Resume/continue, checkpoints/rewind of files | Fork/branch sessions, cross-device/cloud handoff, exportable transcripts |
| D10 | Multi-agent & background tasks | Sub-agents, parallelism, long-running jobs. | None | One helper agent | Typed sub-agents with own tools/context, background shells | Parallel fan-out, agent-to-agent messaging, scheduled/remote agents, worktree isolation |
| D11 | Extensibility | MCP, plugins, skills, hooks, custom commands. | None | Custom prompts only | MCP + hooks + custom commands | Plugin packaging/marketplace, skills with progressive disclosure, lifecycle hooks with decision control |
| D12 | Desktop/IDE host integration | How the harness embeds in editors or a desktop app. | Terminal only | Basic IDE extension | Diff views, selection/diagnostics sharing, multi-session UI | Host-provided tools (preview, editor open, PR status), split panes, protocol for third-party hosts |
| D13 | Computer/browser use | Acting on GUIs and web pages. | None | Screenshot-only browsing | Browser automation (DOM + screenshots) or desktop control | Both, with per-site permissions, accessibility-tree refs, batching |
| D14 | Safety & prompt-injection defenses | Guardrails against harmful or injected actions. | None | Prompt-level warnings | Untrusted-content boundaries, approval for risky actions, secret handling | Layered: classifier checks, provenance tracking, enforced deny lists, admin policy |
| D15 | Observability & UX of agent work | Making agent work reviewable. | Raw log | Readable transcript | Inline diffs, todo/plan display, notifications, cost/usage | Review workflows (PR/diff review), checkpoints, telemetry export (OTel), structured summaries |

## Scorecard template

```markdown
| Dim | Claude Code | <vendor> | Notes |
|---|---|---|---|
| D1 | | | |
...
| D15 | | | |
```
