# 04 — Permissions, sandbox, approvals (Claude Code, public docs 2026-10)

## Overview
Two complementary layers. **Permissions** decide, per tool call, whether to
allow, ask, or deny — driven by a session *mode*, declarative rules from
layered settings, hooks, and (in auto mode) a model-based classifier.
**Sandboxing** is OS-enforced containment of shell commands (filesystem
writes + network egress through a filtering proxy), which lets sandboxed
commands run without prompts. Hard "circuit breakers" (protected paths,
critical-path removals) sit beneath both and cannot be allow-listed away.

## Diagram
```
tool call
  │
  ├─ PreToolUse hooks (exit 2 → block before rules; may return allow/deny/ask/defer)
  ├─ rules: DENY ▸ ASK ▸ ALLOW  (first match in that order; specificity ignored;
  │         merged across managed / CLI / local / project / user)
  ├─ circuit breakers: protected-path writes, rm/rmdir on critical paths,
  │         requiresUserInteraction MCP tools, network-path reads
  ├─ mode:  default(Manual) │ acceptEdits │ plan │ auto │ dontAsk │ bypassPermissions
  │            ask            edits ok     read-only  classifier  deny-if-would-ask  allow
  ├─ PermissionRequest hooks (may decide on the user's behalf)
  └─ execute ── Bash/PowerShell/Monitor ─► OS sandbox (Seatbelt | bubblewrap+socat)
                                            fs write allowlist · net via proxy
```

## Mechanics

### Modes
| Mode | Runs without asking |
|---|---|
| `default` (Manual) | reads in working dirs, read-only Bash set |
| `acceptEdits` | + file edits and common fs commands (`mkdir`, `touch`, `mv`, `cp`) in working dirs |
| `plan` | exploration only; no source edits; plan presented via `ExitPlanMode` for approval |
| `auto` | most actions; a classifier reviews the rest |
| `dontAsk` | only pre-approved tools; anything that would prompt is denied |
| `bypassPermissions` | everything except the non-bypassable actions |

Cycle with Shift+Tab; `defaultMode` setting; `--permission-mode`. Docs say
auto is the built-in starting mode for interactive terminal/VS Code sessions
on recent versions. Admins can disable bypass/auto
(`disableBypassPermissionsMode`, `disableAutoMode`).

### Rules
- Syntax `Tool` or `Tool(specifier)`: `Bash(npm run *)` (command patterns,
  compound commands split and each part checked), `Read(~/secrets/**)` /
  `Edit(/src/**)` (gitignore-style paths; Read rules also govern Grep/Glob/
  LSP and shell reads), `WebFetch(domain:example.com)`, `mcp__server__tool`
  (globs allowed), `Agent(Explore)`, `Skill(deploy *)`, input-parameter
  matches such as `Bash(dangerouslyDisableSandbox:true)`.
- Order: deny → ask → allow; a broad deny beats a narrow allow; a deny at
  any scope beats an allow at any scope.
- "Yes, don't ask again" persists per repo+command for Bash, per
  repo+domain for WebFetch; edits only for the session.
- Working dirs: cwd plus `--add-dir` / `/add-dir` /
  `additionalDirectories` (grant file access, not configuration). `/cd`
  moves a session.
- Workspace trust: project allow rules and some project-level executables
  (hooks etc.) are gated on trusting the folder.

### Auto mode classifier
- Decision order: (1) explicit rules (with exceptions: protected-path
  writes, critical-path removals, consent-required MCP tools, content-scoped
  ask rules fall back to prompt); (2) read-only actions and in-cwd edits
  auto-approved (first read outside working dirs prompts); (3) everything
  else → classifier; (4) on block, the model gets the matched rule category.
- Blocks by default: download-and-execute, exfiltration of sensitive data,
  prod deploys/migrations, mass cloud deletion, IAM/repo permission grants,
  shared-infra changes, destroying pre-existing files, force push,
  destructive git resets/cleans, amending commits not made this session.
  Trusts the working dir and remotes configured at session start; admins
  extend "trusted infrastructure".
- Entering auto mode **drops broad allow rules** that equal arbitrary code
  execution (`Bash(*)`, interpreter wildcards, package-manager run, `Agent`,
  `Monitor`), restored on exit.
- The classifier sees user messages, non-read-only tool calls, and CLAUDE.md
  — **tool results are stripped**, so injected content can't argue with it.
  A separate probe scans incoming tool results for injection.
- Before discarding-work commands the harness runs `git status` itself and
  shows the classifier what would be lost.
- Fallback: 3 consecutive or 20 total blocks → auto pauses and prompting
  resumes (not configurable). Unparseable verdict → deny. Can run
  server-side as part of the model request, with client-side fallback.
- Denials listed under `/permissions` → Recently denied, retryable manually.

### Non-bypassable protections
- **Protected paths** (never auto-approved except in bypass): `.git`,
  `.claude` config, skills/agents/commands/hooks dirs, `.mcp.json`, shell rc
  files, `.gitconfig`, IDE dirs, etc.
- **Critical paths** for `rm`/`rmdir`: filesystem root, top-level dirs, home,
  working dir and its parents, Windows drive roots — no allow rule or hook
  can approve these.

### OS sandbox
- macOS: Seatbelt. Linux/WSL2: bubblewrap + socat (+ seccomp filter). Native
  Windows: unsandboxed. Built on the open-source `sandbox-runtime` package.
- Writes: cwd, per-user temp, added dirs; protected config paths stay
  write-denied even inside them (prevents self-granting permissions or
  planting hooks/MCP servers that run unsandboxed). Linked worktrees may
  write the shared `.git` except `hooks/` and `config`.
- Reads: most of the machine by default (credential files included unless
  `denyRead`/credential masking is configured).
- Network: no direct route; all traffic through a local proxy checked
  against `allowedDomains` (starts empty); unknown hosts handled per mode.
- Modes: *auto-allow* (sandboxed commands run without prompt; replaces a bare
  `Bash` ask rule) vs *regular permissions*. Content-scoped ask rules, deny
  rules and critical-path checks still apply.
- Escape hatch: on sandbox failure Claude may retry with
  `dangerouslyDisableSandbox`, which goes through normal permissions;
  `allowUnsandboxedCommands: false` disables it. `excludedCommands` always
  run outside. `failIfUnavailable` makes missing sandbox fatal.
- Credential protection: env-var masking, credential-file masking, request
  re-signing for AWS.
- Not sandboxed: file tools, MCP servers, hooks.

## Harness design takeaways
- **T1** — Deny-first, specificity-blind rule evaluation is easy to reason
  about and safe to merge across scopes.
- **T2** — Reuse one rule grammar for all tools, with tool-specific
  specifiers (command globs, gitignore paths, domains, input params).
- **T3** — A model-based classifier for "auto" should see *intent and
  actions*, not tool results — that is the injection firewall.
- **T4** — When switching to a more autonomous mode, *narrow* the user's
  existing allowlist automatically.
- **T5** — Bound classifier autonomy with block counters that fall back to
  human prompting.
- **T6** — Hard-code a small set of circuit breakers (critical rm targets,
  self-config writes) that no rule or hook can override.
- **T7** — OS sandbox + egress proxy lets you trade prompts for containment;
  protect the harness's own config inside the sandbox or it can be escaped.
- **T8** — Gate project-provided executables/permissions on workspace trust.

## Open questions
- Classifier model identity and prompt are not published (engineering blog
  describes the approach). (uncertain)
- Precise semantics of `defer` from PreToolUse and of mods' `tool.check`
  override order may evolve.
