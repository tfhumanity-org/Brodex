# 10 — Safety, injection defenses, observability (Claude Code, public docs 2026-10)

## Overview
Safety is defense-in-depth across the model (training + core prompt
guidance), the harness (permissions, classifier, circuit breakers, sandbox,
provenance labels on untrusted content), and the organization (managed
settings, managed MCP, provider restrictions). Observability serves both the
individual (inline diffs, plan/task panes, `/context`, `/usage`, status line,
notifications, transcripts) and the org (OpenTelemetry metrics/events,
analytics dashboards, cost tracking).

## Diagram
```
untrusted inputs: files · web pages · tool/MCP results · screen · other agents · repo config
      │
      ├─ provenance: reminders/labels mark content as data; inter-agent msgs labelled
      ├─ probe scans incoming tool results; subagent reports scanned before parent reads
      ├─ auto classifier sees intent+actions, not tool results
      ├─ circuit breakers: protected paths, critical rm targets, consent-required tools
      ├─ OS sandbox: fs write allowlist + egress proxy; credential masking
      ├─ workspace trust gates project hooks/permissions/MCP; repo can't enable browser
      └─ managed policy: deny rules, disable bypass/auto, allowed providers/MCP, sandbox lock
observability: transcript JSONL · diffs · tasks/plan · /context · /usage · statusline JSON
               · notifications · OTel metrics+events · admin analytics · hooks as audit taps
```

## Mechanics

### Prompt-injection posture (public)
- Instruction files are advisory; enforcement belongs to rules/hooks.
- WebFetch passes pages through an extraction prompt rather than raw HTML.
- Auto-mode classifier inputs exclude tool results; a separate server-side
  probe scans incoming tool results and flags suspicious content.
- Subagent final reports are scanned; suspicious text flagged, not removed.
- Inter-agent and cross-session messages are labelled as non-user; cannot
  grant approvals; classifier reviews them and treats relayed approvals as
  untrusted.
- Script-computed prompts (workflows) are not treated as user intent.
- Keyword triggers (e.g. starting a workflow) only honored from
  human-typed input, not from relayed webhooks/PR comments.
- Computer use: terminal excluded from screenshots, consumed Esc, per-app
  approval.
- Browser: per-site permission, pauses at login/CAPTCHA, credential-file
  upload refusal.
- Repo-sourced config that widens trust (browser enablement, some keys) is
  ignored; hooks and project allow rules gated on folder trust.
- Model-side: automatic fallback model on safety-classifier flags (some
  models); `EndConversation` for rare sustained abuse.

### Organizational controls
- Managed settings (file, MDM, server-managed from admin console) override
  all scopes; `allowManagedPermissionRulesOnly`, `disableBypassPermissionsMode`,
  `disableAutoMode`, auto-mode "trusted infrastructure" config, sandbox
  enforcement and anti-widening locks, `allowedProviders`, login forcing,
  `managed-mcp.json` / MCP allow & deny lists, plugin marketplace controls,
  `disableWorkflows`, desktop-specific keys, HIPAA/ZDR configurations.
- `--restricted` session flag (classifier cannot approve protected writes;
  removes Bash).

### Security tooling for code
- `/security-review` command, security-guidance plugin (catches issues as
  code is written), Claude Security scanning, `/code-review`, cloud
  "ultrareview", GitHub Code Review app.

### Observability for the user
- Inline diffs per edit; desktop diff pane; tasks pane fed by Task tools;
  plan pane; subagent pane; workflow progress view with per-agent tokens.
- `/context` (category breakdown), `/usage` (cost, cache hit ratio, likely
  miss cause), `/status` (credential, org, model), `/doctor`, `/debug` logs.
- Status line script receives session JSON (model, cost, context, cache).
- Notifications: OS notifications, `Notification` hook, terminal escape
  sequences, `PushNotification` tool to phone via Remote Control.
- `/export`, transcripts, `--output-format stream-json`.
- `/permissions` with "Recently denied" list and retry.

### Observability for the org
- OpenTelemetry export of metrics (sessions, tokens incl. cache read/write,
  cost, lines changed, commits/PRs, tool decisions) and events (prompts, tool
  results, API requests/errors), with opt-in content logging. (metric names
  intentionally omitted here.)
- Admin analytics dashboards; plugin usage measurement.
- Hooks (`http` type) double as audit/event taps.

## Harness design takeaways
- **T1** — Label provenance on every non-user input (files, web, tools,
  agents, scripts) and never let labelled data carry approval authority.
- **T2** — Keep the safety judge blind to tool output; judge actions against
  user intent.
- **T3** — Scan at boundaries: tool results entering context and worker
  reports entering a parent.
- **T4** — Org policy must sit above every other scope, including CLI flags,
  with explicit anti-widening locks.
- **T5** — Make context and cost visible in-session (`/context`, `/usage`,
  status line) — users manage what they can see.
- **T6** — Export OTel by default-off, content-off; let orgs opt in.
- **T7** — A "recently denied" list with one-key retry turns false positives
  from a dead end into a reviewable queue.

## Open questions
- Classifier/probe models and thresholds are not public. (uncertain)
- Exact OTel schema is versioned; consult the monitoring page when comparing.
