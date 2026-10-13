# Claude Code harness — reference spec

Baseline spec of Anthropic's Claude Code harness (CLI, IDE extensions, and the
Code tab of the Claude desktop app), written from public documentation
(code.claude.com/docs, snapshot 2026-10) and first-hand use as the agent
running inside it. Every target in `targets/<vendor>/spec/` is compared
against this baseline using `audit/RUBRIC.md`.

Conventions:
- Follows the `CLEANROOM.md` template (Overview, Diagram, Mechanics,
  Takeaways T1..Tn, Open questions).
- Only documented / user-visible behavior. Items marked **(uncertain)** are
  inferred from use or are likely to drift between releases.
- Version numbers like "v2.1.2xx" are cited only when docs tie a behavior to
  them; the product ships weekly, so treat specifics as perishable.

| File | Topic | Rubric dims |
|---|---|---|
| [00-desktop-architecture.md](00-desktop-architecture.md) | Surfaces, process model, environments (local/cloud/SSH/remote) | D12 |
| [01-agent-loop-and-api.md](01-agent-loop-and-api.md) | Agentic loop, streaming, interrupts, models, effort, caching | D1, D2 |
| [02-system-prompt-and-context.md](02-system-prompt-and-context.md) | Prompt layers, CLAUDE.md/AGENTS.md, rules, memory, reminders | D3 |
| [03-tools.md](03-tools.md) | Built-in tool surface, file/shell tools, deferred tools | D4, D5 |
| [04-permissions-sandbox-approvals.md](04-permissions-sandbox-approvals.md) | Modes, rules, auto-mode classifier, OS sandbox | D6, D7 |
| [05-context-management-and-sessions.md](05-context-management-and-sessions.md) | Compaction, transcripts, resume/fork, checkpoints | D8, D9 |
| [06-multi-agent-and-background.md](06-multi-agent-and-background.md) | Subagents, forks, teams, background tasks, workflows, scheduling | D10 |
| [07-extensibility.md](07-extensibility.md) | MCP, skills, hooks, plugins, settings layers | D11 |
| [08-desktop-coding-host.md](08-desktop-coding-host.md) | Desktop Code tab: worktrees, diff, preview, PR, terminal | D12, D15 |
| [09-computer-use.md](09-computer-use.md) | Chrome integration, computer use, simulators | D13 |
| [10-safety-and-observability.md](10-safety-and-observability.md) | Injection defenses, admin policy, telemetry, review UX | D14, D15 |

Primary sources (public): How Claude Code works, Tools reference, Permissions,
Permission modes, Sandboxing, Memory, Context window, Prompt caching,
Sessions, Sub-agents, Skills, Hooks, Settings, Desktop, Chrome, Computer use.
