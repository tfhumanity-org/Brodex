# 02 — System prompt assembly and instruction files (Claude Code, public docs 2026-10)

## Overview
Context is assembled from a fixed core system prompt, environment facts,
layered instruction files written by humans (CLAUDE.md / AGENTS.md / rules),
notes written by the agent itself (auto memory), short listings of available
capabilities (skill descriptions, deferred tool names), and in-conversation
"system reminders" the harness injects as work proceeds. Instruction files
are *context, not enforcement* — the docs point users to hooks and
permission rules for hard guarantees.

## Diagram
```
[system layer]   core prompt (behavior, tool use, formatting)
                 + tool definitions (built-in; MCP deferred → names only)
                 + output style instructions
[project layer]  env info (cwd, platform, shell, OS, git repo?)
                 + git snapshot (branch, status, recent commits)
                 + auto memory MEMORY.md (first 200 lines / 25 KB)
                 + skill descriptions (≤1,536 chars each)
                 + CLAUDE.md chain: managed → user → ancestors…cwd (+ .local)
                 + unscoped .claude/rules/*.md
[conversation]   user msgs, assistant msgs, tool results
                 + system reminders (file changed, nested CLAUDE.md on demand,
                   path-scoped rules on demand, plan-mode text, skill bodies,
                   attribution lines, task-list nudges …)
```

## Mechanics

### Core prompt
- Not user-visible. Covers behavior, tool usage policy, response format,
  git/PR conventions (removable with `includeGitInstructions: false`), and
  commit/PR attribution (configurable via `attribution`).
- SDK/CLI flags: `--system-prompt` (replace), `--append-system-prompt`
  (append), file variants. Resumed conversations keep their original system
  prompt until compaction (cache preservation).
- Output styles (`/output-style`) swap or extend the presentation layer;
  a mid-session switch is delivered as a message.

### Instruction files (CLAUDE.md family)
| Scope | Location |
|---|---|
| Managed policy | `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS), `/etc/claude-code/CLAUDE.md` (Linux/WSL), `C:\Program Files\ClaudeCode\CLAUDE.md` |
| User | `~/.claude/CLAUDE.md` |
| Project | `./CLAUDE.md` or `./.claude/CLAUDE.md` |
| Local (personal, gitignored) | `./CLAUDE.local.md` |

- All files from filesystem root down to cwd are **concatenated**, root
  first, so closer files are read last; `.local` appended per directory.
- Subdirectory CLAUDE.md files load **on demand** when Claude reads/edits a
  file in that subtree (including reads via `cat`/`head`).
- `@path` imports, relative to the importing file, recursive to depth 4;
  ignored inside code spans/blocks.
- Block-level HTML comments are stripped before injection.
- `claudeMdExcludes` skips unwanted files in monorepos.
- **AGENTS.md**: read only when no CLAUDE.md / .claude/CLAUDE.md /
  CLAUDE.local.md exists in cwd or ancestors (user/managed files don't count);
  a setting can load both. Not read: `AGENTS.local.md`, `AGENTS.override.md`,
  `.agents/`.
- `/init` drafts a CLAUDE.md; `/memory` opens the loaded files.

### Rules (`.claude/rules/*.md`)
Topic files; with `paths:` frontmatter (globs) they load only when a matching
file is touched; otherwise they load at start like CLAUDE.md. User-level rules
in `~/.claude/rules/`. Symlinks allowed for sharing.

### Auto memory
- Agent-written notes at `~/.claude/projects/<project>/memory/` (`MEMORY.md`
  index plus topic files); `<project>` derived from the git repo, so
  worktrees share it. Relocatable with `autoMemoryDirectory`.
- First 200 lines or 25 KB of MEMORY.md load every session.

### System reminders
Harness-authored context blocks appended to the conversation, e.g. a file
read earlier changed on disk, CLAUDE.md content, output style text, git
attribution lines, nested instructions on demand. They keep the cached prefix
intact. (Observed in use: also used for deferred-tool announcements and
background-task completion notices.)

### What survives compaction
Project-root CLAUDE.md, unscoped rules, auto memory, and the plan file are
re-read from disk; path-scoped rules and nested CLAUDE.md are re-loaded on
demand; skill bodies re-injected (≤5,000 tokens each, 25,000 total, oldest
dropped). See `05`.

## Harness design takeaways
- **T1** — Hierarchical, concatenated instruction files (org → user →
  repo ancestors → cwd → local) beat a single file: teams, individuals, and
  admins each get a lane without editing each other's text.
- **T2** — Lazy, path-triggered loading (nested CLAUDE.md, `paths:` rules)
  keeps monorepo context small while still delivering local conventions.
- **T3** — Honour the cross-vendor `AGENTS.md` convention as a fallback, but
  pick a clear precedence so two files don't double-instruct.
- **T4** — Give the agent its own bounded, per-repo memory file distinct from
  human-written instructions; cap what loads at start.
- **T5** — Deliver dynamic context as appended reminders, never by editing
  the system prompt, to preserve caching.
- **T6** — Strip maintainer comments before injection so humans can annotate
  instruction files for free.
- **T7** — Explicitly tell users instructions are advisory and route hard
  guarantees to hooks/permissions.

## Open questions
- Exact section ordering and size of the core prompt are not published;
  docs' context simulator shows ~4k tokens as representative. (uncertain)
- How conflicts between managed and project CLAUDE.md are weighted beyond
  ordering is unspecified.
