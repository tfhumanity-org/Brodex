# 03 — Tool surface (Claude Code, public docs 2026-10)

## Overview
Claude Code exposes a mid-sized set of purpose-built tools with JSON schemas,
named with stable strings that are reused verbatim in permission rules, hook
matchers, and subagent tool lists. File editing is exact-string replacement
with read-before-edit guards; shell is a single `Bash` tool with persistent
cwd, captured shell profile, timeouts, auto-backgrounding, and spill-to-file
output. External tools come from MCP and are *deferred* behind `ToolSearch`
so the prompt only carries their names until needed.

## Diagram
```
             ┌ file ───── Read · Write · Edit · NotebookEdit
             ├ search ─── (Glob · Grep on Windows; else find/grep via Bash)
             ├ exec ───── Bash · PowerShell · Monitor · TaskOutput/TaskStop
 tool set ───┼ web ────── WebFetch · WebSearch
             ├ code intel  LSP (via code-intelligence plugins)
             ├ planning ─ EnterPlanMode · ExitPlanMode · TaskCreate/Get/List/Update (TodoWrite legacy)
             ├ agents ─── Agent · SendMessage · ListAgents · Workflow · SubagentHandback
             ├ worktree ─ EnterWorktree · ExitWorktree
             ├ user I/O ─ AskUserQuestion · PushNotification · SendUserFile · Artifact
             ├ schedule ─ CronCreate/Delete/List · ScheduleWakeup · RemoteTrigger
             ├ MCP ────── ToolSearch · ListMcpResourcesTool · ReadMcpResourceTool · WaitForMcpServers
             └ misc ───── Skill · ReportFindings · SendFeedback · EndConversation
```

## Mechanics

### Permission defaults (Manual mode, inside working dirs)
No prompt: `Read`, `Glob`, `Grep`, `LSP`, `AskUserQuestion`, task/cron tools,
`ToolSearch`, `EnterPlanMode`. Prompt: `Bash` (except a built-in read-only
command set), `Write`, `Edit`, `NotebookEdit`, `WebFetch` (except
preapproved documentation domains), `WebSearch`, `Skill`, `ExitPlanMode`,
`Workflow`. File tools still prompt outside the working/additional dirs.

### File tools
- **Read**: ≤25,000 tokens per call by default (env-tunable); refuses whole
  files >256 KB and asks for ranges (`offset`/`limit`); partial reads are
  flagged as a "partial view". Reads images, PDFs, notebooks (≤256 KB cells).
- **Edit**: exact `old_string` → `new_string`, no regex/fuzzy; must be unique
  unless `replace_all`. Read-before-edit required (newer models may skip it
  when a read would not prompt). A file changed on disk since the last read
  can still be edited if the match is exact and unambiguous; the result warns
  that other changes exist. Read-deny rules also block edits.
- **Read via shell counts**: `cat`, `head`, `tail`, `sed -n 'X,Yp'`, `grep`,
  `rg`, etc. on a single file with no pipes satisfy read-before-edit and
  trigger nested CLAUDE.md / path-rule loading.
- **Write**: full overwrite, UTF-8 output; same read-before-overwrite policy.
- File snapshots are taken before edits for checkpoint/rewind (see `05`).

### Shell (`Bash`)
- `cd` persists while inside project/additional dirs, otherwise resets with
  a notice; env vars do not persist (use `CLAUDE_ENV_FILE` or a SessionStart
  hook); aliases/functions from the user's rc file are captured at start.
- Timeout: default 2 min, max 10 min (env-tunable). On foreground timeout the
  command is **moved to background** rather than killed (except `sleep`).
- Background commands (`run_in_background`): 30 min default, 2 h max; output
  goes to a file Claude reads later; completion notices arrive as reminders.
- Output: ~30,000 chars inline; larger output saved to a session file (cap
  64 MiB) with a 2,000-char preview; failures get a head+tail excerpt
  (~10,000 chars). `BASH_MAX_OUTPUT_LENGTH` up to 150,000.
- On macOS/Linux, `find`/`grep` inside Claude's shell are embedded fast
  implementations (bfs, ugrep), which is why Glob/Grep are dropped there.
- `dangerouslyDisableSandbox` parameter requests an unsandboxed retry (see `04`).
- Memory limit applies on Linux/WSL.

### Monitor
Runs a command (or opens a WebSocket) in the background and streams each
output line/message back as an event so Claude can react mid-conversation;
`timeout_ms` deadline; stoppable via `TaskStop`.

### Web
- **WebFetch**: URL + extraction prompt; HTML → Markdown, then a separate
  small-model call answers the prompt (lossy by design). Refuses localhost and
  dotless hosts; HTTP→HTTPS; ≤100,000 chars per call with continuation
  offset; 15-min cache; 5-min deadline; cross-host redirects returned, not
  followed.
- **WebSearch**: server-side search, per-session limit.

### Tool loading
- MCP tools (and some built-ins in some hosts) are **deferred**: only names
  are listed; `ToolSearch` (`select:` exact or keyword) returns full schemas
  which then become callable. `ENABLE_TOOL_SEARCH=auto|false` changes this.
- Tool availability is configurable per session: `--tools`,
  `--allowedTools`, `--disallowedTools`, `--restricted`, subagent `tools`.

### Task tracking
`TaskCreate/Get/List/Update` (dependencies, status) replace `TodoWrite` on
newer models; the UI renders the list in a tasks pane.

## Harness design takeaways
- **T1** — One canonical tool-name string reused across permissions, hooks,
  and agent configs makes policy composable.
- **T2** — Exact-match edits + uniqueness + read-before-edit is a simple,
  verifiable contract; allow stale-file edits only when the match proves safety.
- **T3** — Never kill a slow foreground command — background it and hand back
  a handle; it removes a whole class of "timeout destroyed my build" failures.
- **T4** — Spill large outputs to files with a preview instead of truncating
  silently; let the model grep the spill.
- **T5** — Defer rarely-used tool schemas behind a search tool to protect
  context and cache.
- **T6** — Treat shell-based reads as reads (for guards and lazy context
  loading) rather than fighting the model's habit of using `cat`.
- **T7** — Make web fetch a summarize-through-a-prompt operation by default to
  bound context and reduce raw-HTML injection surface; keep `curl` as escape.
- **T8** — Event-streaming monitor tool turns logs/websockets into
  interruptible inputs for the loop.

## Open questions
- The exact read-only Bash allowlist and the preapproved WebFetch domain list
  are documented but change frequently.
- Concurrency semantics between `Bash` and file tools in the same batch.
  (uncertain)
