# 05 — Context management and sessions (Claude Code, public docs 2026-10)

## Overview
Every session starts with a fresh context window. The harness keeps long
sessions alive through layered mechanisms: deferred tool schemas, lazy
instruction loading, tool-output spilling, clearing of old tool outputs,
automatic and manual compaction with deterministic re-hydration, and
delegation of bulky reads to subagents. Durability comes from an append-only
JSONL transcript per session, which enables resume, fork/branch, and
checkpoint-based rewind of both conversation and files.

## Diagram
```
 context fills ─► clear old tool outputs ─► auto-compact (summary request,
                                             same prefix → cache read)
                                                │
               re-hydrate: root CLAUDE.md, unscoped rules, auto memory,
               plan file, fresh git snapshot, ≤5 recent files (≤5k tok each),
               skill bodies (≤5k each / 25k total), running-task reminders,
               SessionStart(compact) hook output
 transcript: ~/.claude/projects/<project>/<session-id>.jsonl  (append-only)
     ├─ --continue / --resume <id|name|path>  → same id, append
     ├─ /branch, --fork-session               → copy, new id
     └─ /rewind (Esc Esc) → restore code | conversation | both | summarize from/up to here
 file snapshots before each turn-starting prompt (last 100 checkpoints)
```

## Mechanics

### Context budget levers
- MCP tools deferred: only names + server instructions until `ToolSearch`.
- Skills: one-line descriptions only; body on invocation.
- Nested CLAUDE.md and `paths:` rules load on demand.
- Large shell output spilled to file (see `03`); Read capped at 25k tokens.
- Subagents read in their own window and return a summary.
- `/context` shows a live breakdown by category; `/usage` shows cost/cache.
- Images/PDFs: oldest evicted in batches when request limits are reached.

### Compaction
- Auto-compaction near the window limit; threshold per model, adjustable
  with `/autocompact <tokens>`. 1M-context variants compact the same way.
- First step is clearing older tool outputs, then summarizing.
- The summary request reuses the conversation's system prompt, tools, and
  history plus an appended summarization instruction, so it reads the warm
  cache; it inherits the session's extended-thinking setting.
- `/compact [focus]`; a "Compact Instructions" section in CLAUDE.md steers
  automatic summaries. `PreCompact`/`PostCompact` hooks.
- Partial: `/rewind` → "Summarize from here" / "Summarize up to here" with
  optional guidance; originals stay in the transcript.
- Thrash guard: stops auto-compacting after a few immediate refills.
- `/recap` shows a summary without replacing history (cache-safe).
- `/clear` starts a fresh conversation; previous one remains resumable.

### Transcripts
- JSONL at `~/.claude/projects/<project>/<session-id>.jsonl`; `<project>` is
  the cwd with non-alphanumerics replaced by `-` (truncated + hashed >200
  chars). Format is internal and unstable; use `/export`, `-p --output-format
  json`, hooks' `transcript_path`, or the SDK instead.
- Retention: 30 days default (`cleanupPeriodDays`); `claude purge`;
  `--no-session-persistence` for one `-p` run.
- Subagent transcripts under `<session>/subagents/agent-<id>.jsonl`.

### Resume / fork
- `--continue` (latest in cwd), `--resume` (picker, id, name, or path),
  `--from-pr <n>`, `/resume`. Picker widens to worktrees (Ctrl+W) or all
  projects (Ctrl+A); branch filter; preview; rename.
- Restored on resume: history, model, agent, permission mode (with
  exceptions: bypass is never silently restored), active goal, unexpired
  scheduled tasks. Not restored: background shells/monitors (shown as
  unfinished notes), flags like `--mcp-config`, `--add-dir`.
- A tool cut off by a crash is marked so the model verifies before re-running.
- Resuming a large, cold session (>~1 h idle, >100k tokens) on Pro/Max offers
  "resume from summary" vs full.
- Naming: `-n`, `/rename`, auto-generated titles from a small model,
  plan-accept titles.
- `/branch [name]` copies the transcript and switches the live process
  (session grants carry over); `--fork-session` starts a new process (grants
  don't). Resuming the same session in two terminals interleaves writes.
- Background sessions: resuming one that is still running attaches instead.

### Checkpoints
- Snapshot before each prompt that starts a turn; 100 most recent kept;
  persisted with the session; swept with retention.
- Tracks only Claude's file-tool edits — not Bash side effects, not external
  edits, mostly not subagent edits (foreground forked skills excepted), not
  symlinked/hard-linked files.
- Rewinding conversation truncates to an already-cached prefix (cache-cheap).

## Harness design takeaways
- **T1** — Compaction should be *re-hydrating*: summarize the dialog, then
  deterministically reload the durable context (instructions, memory, plan,
  hot files) from disk with explicit budgets.
- **T2** — Run the summarizer on the same prefix as the conversation so
  compaction is mostly a cache read.
- **T3** — Offer partial, user-guided summarization anchored at a message,
  not just all-or-nothing compaction.
- **T4** — Append-only JSONL per session makes resume, fork, export, and
  hooks cheap; declare the format internal and give stable export APIs.
- **T5** — Distinguish *branch in-process* (keeps grants) from *fork new
  process* (fresh grants) — permissions are part of session state.
- **T6** — Checkpoints are scoped to what the harness itself wrote; be
  explicit that shell side effects are not undoable.
- **T7** — On resume, never silently restore the most permissive mode.

## Open questions
- Exact auto-compact thresholds per model are documented on the model-config
  page but change frequently. (uncertain)
- How "clear older tool outputs" chooses what to drop is not specified.
