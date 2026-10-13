# 07 — Extensibility: settings, MCP, skills, hooks, plugins (Claude Code, public docs 2026-10)

## Overview
Extension points are layered by *what they change*: **settings** (config and
policy), **CLAUDE.md/rules** (advisory context, see `02`), **skills** (on-demand
instructions + files, invoked by model or `/name`), **subagents** (`06`),
**MCP servers** (new tools/resources/prompts), **hooks** (deterministic code at
lifecycle events, with decision control), **plugins** (packages bundling all of
the above, distributed via marketplaces), and **mods** (plugin components that
change the host UI or intercept tool checks). Output styles, status line,
keybindings, and channels round it out.

## Diagram
```
 managed ▸ CLI --settings ▸ .claude/settings.local.json ▸ .claude/settings.json ▸ ~/.claude/settings.json
   (highest; can lock keys)                                       (lists like permissions.allow merge)
                        │
     ┌──────────────────┼────────────────────────────────────────────┐
  skills/          agents/        hooks             MCP servers            plugins (marketplace)
  SKILL.md         *.md           settings.hooks    .mcp.json / ~/.claude.json   .claude-plugin/plugin.json
  desc in ctx,     own ctx        command|http|     stdio|http|sse, OAuth  bundles skills, agents, hooks,
  body on use                     mcp_tool|prompt|  tools deferred         MCP, LSP, monitors, themes,
                                  agent             via ToolSearch         workflows, mods
```

## Mechanics

### Settings
- Precedence: managed (MDM / managed-settings.json / server-managed from the
  admin console) > CLI `--settings` > project-local > shared project > user.
  Some keys are honored only from user/managed scope (a repo can't set them —
  e.g. enabling browser integration). List-valued permission keys merge across
  scopes unless an org restricts to managed rules only.
- `~/.claude.json` holds app-written global state (sign-in, MCP configs,
  per-project trust).
- `env` block applies env vars; most settings hot-reload; `/config` UI;
  `/status`, `/doctor` for diagnosis.

### MCP
- Transports: stdio, streamable HTTP, SSE (legacy). OAuth for remote
  servers. Scopes: local (per-user per-project), project (`.mcp.json`,
  checked in, requires approval), user. `claude mcp add|list|remove`, `/mcp`.
- claude.ai connectors can be used in the CLI when signed in with a
  subscription.
- Tools named `mcp__<server>__<tool>`; deferred by default (names only,
  `ToolSearch` to load); `WaitForMcpServers` for late connections; dynamic
  tool updates; resources via list/read tools; MCP prompts surface as
  slash commands; elicitation supported (with hooks).
- Per-tool `requiresUserInteraction` forces a prompt even in permissive modes.
- Org control: `managed-mcp.json` (exclusive set), `allowedMcpServers` /
  `deniedMcpServers`.
- Built-in MCP servers: browser (`claude-in-chrome`) and `computer-use` (see `09`).
- Claude Code can itself run as an MCP server (`claude mcp serve`).

### Skills
- Directory with `SKILL.md` (frontmatter: `name`, `description`,
  `when_to_use`, `disable-model-invocation`, `user-invocable`,
  `allowed-tools`, `model`, `effort`, `context: fork` + `agent`,
  `background`, `paths`…) plus supporting files.
- Locations: managed > `~/.claude/skills/` > `.claude/skills/` (nested
  dirs load on demand) > plugin (namespaced `plugin:skill`). Legacy
  `.claude/commands/*.md` still create `/commands`; a skill wins on name clash.
- Progressive disclosure: description (≤1,536 chars with `when_to_use`) always
  listed; body loaded via the `Skill` tool on use and kept; keep `SKILL.md`
  under ~500 lines; reference files read on demand.
- `allowed-tools` pre-approves tools for the invoking turn only (not a
  restriction). `context: fork` runs it as a subagent (background by default).
- Bundled skills exist (e.g. code review, simplify, workflow authoring); a
  user skill can replace a bundled one by name.

### Hooks
- Events: session (`SessionStart`, `SessionEnd`, `Setup`), turn
  (`UserPromptSubmit`, `UserPromptExpansion`, `Stop`, `StopFailure`), tool
  (`PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`,
  `PostToolUseFailure`, `PostToolBatch`), agents/tasks (`SubagentStart`,
  `SubagentStop`, `TaskCreated`, `TaskCompleted`, `TeammateIdle`),
  compaction/model (`PreCompact`, `PostCompact`, `PreModelSwitch`,
  `PostModelSwitch`), MCP elicitation, config/files (`ConfigChange`,
  `InstructionsLoaded`, `CwdChanged`, `FileChanged`, `WorktreeCreate`…),
  `Notification`, `MessageDisplay`.
- Matchers: exact names / `|` lists, otherwise regex; per-handler `if` in
  permission-rule syntax.
- Handler types: `command` (stdin JSON; exec form via `args`), `http`
  (POST), `mcp_tool`, `prompt` (single model call → JSON), `agent`
  (experimental verifier subagent). Default timeouts 600 s / 30 s (prompt) /
  60 s (agent).
- Exit 0 = success (stdout JSON parsed); exit 2 = block (stderr is reason) on
  blocking events; other = non-blocking error. `onFailure: "block"` for
  fail-closed.
- Output: `continue:false`, `systemMessage`, `decision:"block"`;
  event-specific: PreToolUse `permissionDecision` (allow/deny/ask/defer),
  `updatedInput`, `additionalContext`; PostToolUse `updatedToolOutput`,
  `classifierContext` for the auto classifier; SessionStart
  `additionalContext`, `initialUserMessage`, `watchPaths`. Context fields
  capped at 10,000 chars (overflow spilled to a file with a 2,000-char preview).
- Hooks never override deny/ask rules; exit-2 blocks precede allow rules.
- Hooks from untrusted project folders are gated on workspace trust;
  `disableAllHooks`.

### Plugins and marketplaces
- Manifest at `.claude-plugin/plugin.json`; components: commands, skills,
  agents, hooks (`hooks/hooks.json`), MCP servers, LSP servers (code
  intelligence → `LSP` tool), monitors, themes, workflows, output styles, mods.
- Marketplaces (git repos / URLs with a marketplace manifest); `/plugin`
  UI; dependencies; org-managed allowlists and recommendations; plugin evals
  (`claude plugin eval`). `/reload-plugins` applies changes; cache cost depends
  on component type (MCP changes behave like server changes).
- **Mods**: plugin components that draw in the host UI (panes, bands, status,
  toasts, commands) and react to events, including a `tool.check` handler that
  runs after rules and hooks and can replace their answer. (newer; uncertain
  stability)

### Other surfaces
- Output styles (replace/extend presentation instructions), status line
  command (receives session JSON incl. cost/cache fields), keybindings file,
  **channels** (push external events into a running session), deep links
  (`claude://…`), Agent SDK (TS/Python) with the same hooks/permissions/MCP.

## Harness design takeaways
- **T1** — Separate advisory (CLAUDE.md, skills) from deterministic (hooks,
  rules) extension points and say so explicitly.
- **T2** — Progressive disclosure for skills and MCP keeps the always-on cost
  to a one-liner per capability.
- **T3** — A rich, typed hook protocol (exit codes + JSON decisions + input
  rewriting + context injection) turns the harness into a policy engine.
- **T4** — Hooks must not be able to weaken deny rules; fail-closed option
  for security hooks.
- **T5** — Package everything as plugins with namespacing and marketplaces;
  make org policy able to allowlist/deny sources.
- **T6** — Repos can propose but not escalate: keys that widen trust are
  ignored from project scope and gated on workspace trust.
- **T7** — Let the harness itself be an MCP server and an SDK so it composes
  in both directions.

## Open questions
- Mods API surface and stability. (uncertain)
- Exact list of keys ignored from project scope is long and versioned.
