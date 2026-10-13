# 00 — Surfaces and process architecture (Claude Code, public docs 2026-10)

## Overview
Claude Code is one agent harness exposed through many surfaces: a terminal
CLI, VS Code and JetBrains extensions, the Code tab of the Claude desktop app,
claude.ai/code (cloud), mobile/Remote Control, Slack, and CI (GitHub Actions,
GitLab). The docs state that the agentic loop, tools, and capabilities are the
same everywhere; surfaces differ in *where code executes* and *how the user
watches and steers*. The same engine is also packaged as the Agent SDK
(TypeScript/Python), which is the programmatic form of the harness.

## Diagram
```
             ┌──────────── surfaces (UI) ────────────┐
 terminal TUI │ VS Code / JetBrains │ Desktop Code tab │ claude.ai/code │ mobile │ Slack │ CI
             └───────────────┬────────────────────────┘
                             │ (local process, SDK protocol, or remote bridge)
                     ┌───────▼────────┐
                     │  agent harness │  loop · tools · permissions · context
                     │  (CLI / SDK)   │  hooks · MCP · skills · subagents
                     └───┬───────┬────┘
           execution env │       │ model API (Anthropic API / Bedrock /
   ┌──────────┬──────────┼──┐    │  Vertex(Agent Platform) / Foundry / gateway)
   local    SSH host   WSL  cloud VM (Anthropic-managed or self-hosted)
```

## Mechanics

### Execution environments
| Environment | Where code runs | Notes |
|---|---|---|
| Local | user's machine | default; full access to files/tools, subject to permissions and optional OS sandbox |
| Cloud | Anthropic-managed VMs or org self-hosted environments | continues after the client closes; uses subscription credentials; restricted mode set (accept-edits / plan / auto) |
| Remote Control | user's machine, steered from browser/phone | execution and files stay local; UI is remote |
| SSH (desktop) | remote host via OpenSSH | needs SFTP, `MaxSessions` ≥ 10, ~1 GB free under `~/.claude/remote/`; 30 s connect timeout default |
| WSL (desktop, Windows) | WSL 2 distro | sandbox works in WSL2, not native Windows |

### Process model (inferred from docs + use)
- The CLI is a long-lived process per session that owns the loop, the tool
  runtime, the transcript writer, and MCP client connections. IDE extensions
  and the desktop app host this engine and render its event stream rather than
  reimplementing the loop. (uncertain about exact IPC; docs only describe the
  SDK's streaming JSON message types and `--output-format stream-json`.)
- Headless mode: `claude -p` runs one prompt non-interactively with
  `--output-format text|json|stream-json`, `--max-turns`, `--max-budget-usd`,
  `--json-schema` for structured output, `--permission-prompt-tool` to route
  approvals to an MCP tool, and `--bare` for a fast minimal start.
- Background sessions ("agent view", `claude agents`, `claude attach <id>`,
  `claude stop <id>`) let a session keep running detached from a terminal.
- Configuration and data root: `~/.claude/` (overridable with
  `CLAUDE_CONFIG_DIR`); per-project state under `~/.claude/projects/<project>/`.

### Authentication / providers
Credential precedence (highest first): cloud provider env flags
(`CLAUDE_CODE_USE_BEDROCK|VERTEX|FOUNDRY`) → `ANTHROPIC_AUTH_TOKEN` →
`ANTHROPIC_API_KEY` → `apiKeyHelper` script → `CLAUDE_CODE_OAUTH_TOKEN` →
Anthropic profiles/federation → subscription OAuth from `/login`. A
self-hosted "Claude apps gateway" sits outside this list and outranks it.
Credentials live in the macOS Keychain or `~/.claude/.credentials.json` (0600).
Admins can force login method/org and restrict providers via managed settings.

### Desktop app shell
The desktop app has Chat, Cowork, and Code tabs. The Code tab runs multiple
sessions in parallel in a sidebar; each session has its own folder, history,
environment, and optional git worktree. Panes (chat, diff, browser, terminal,
file editor, plan, tasks, subagents) are freely arranged and can pop out.
See `08-desktop-coding-host.md`.

## Harness design takeaways
- **T1** — One engine, many skins. Keeping the loop/tool runtime in a single
  process (CLI/SDK) and treating every UI as a client of its event stream
  keeps behavior consistent across terminal, IDE, desktop, and CI.
- **T2** — Separate *where code runs* from *where the user is*. Local, SSH,
  WSL, cloud, and remote-control are environment choices orthogonal to the UI.
- **T3** — Make headless a first-class mode (structured output, budgets,
  turn caps, approval delegation) so the same harness powers CI and SDK apps.
- **T4** — A layered credential resolver with a visible "which credential is
  active" status (`/status`) avoids silent billing/org mix-ups.
- **T5** — A single config root with a relocation env var enables
  multi-account use and multi-tenant embedding.

## Open questions
- Exact transport between the desktop app and its engine processes (stdio,
  sockets, in-process) is not documented. (uncertain)
- How cloud sessions are snapshotted/restored across VM restarts beyond
  "continues after you close the app".
