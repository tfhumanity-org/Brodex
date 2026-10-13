# 01 — Agent loop and model API (Claude Code, public docs 2026-10)

## Overview
The loop is a classic tool-use loop over the Anthropic Messages API: send the
full context, stream the model's response, execute any tool calls it emits,
append results, repeat until the model ends its turn without tool calls. The
docs frame it as three blended phases — gather context, take action, verify
results — with the user able to interrupt or steer at any time. The harness's
job is to supply tools and manage what the model sees; prompt-cache
efficiency shapes many design choices.

## Diagram
```
user prompt ─┐
             ▼
   ┌─► build request = [system+tools | project ctx | conversation | new msg]
   │         │  (cache-friendly order; cache_control markers)
   │         ▼
   │   stream response (text, thinking, tool_use blocks)
   │         │
   │   tool_use? ──no──► end turn ─► Stop hooks (may continue) ─► idle
   │         │yes
   │   PreToolUse hooks → permission check → execute (parallel when safe)
   │         │                                 ↑ queued user msgs read here
   │   PostToolUse hooks → append tool_result (+ system reminders)
   └─────────┘
   Esc = abort current tool/turn ; Enter while busy = queue steering message
```

## Mechanics

### Turn mechanics
- **Interrupt**: `Esc` cancels the running tool call and stops; queued
  messages are sent next.
- **Steer without stopping**: typing while Claude works queues a message;
  it is read as soon as the current tool calls finish, *within the same turn*.
- **Continuation hooks**: `Stop`/`SubagentStop` hooks can block the stop and
  inject `additionalContext`, forcing another iteration (used for "keep going
  until tests pass" policies). A `/goal` feature keeps Claude working toward a
  stated goal with turn/time/token tracking.
- **Interrupted tools on resume**: a tool that was mid-flight when the process
  died is marked as cut off; the model is told to verify whether it took
  effect before re-running it.
- **Thrash guard**: if a single huge output refills context immediately after
  each auto-compaction, auto-compaction stops after a few attempts with an
  error instead of looping.

### Parallelism
- The model may emit several tool calls in one response; read-only calls are
  run concurrently (inferred from use; docs describe parallel subagents and a
  `PostToolBatch` hook event that fires after a batch).
- Long work is pushed off the critical path via background shells, `Monitor`,
  and background subagents (see `06`).

### Models and reasoning controls
- Aliases (`opus`, `sonnet`, `haiku`, `fable`) or full IDs; `/model`,
  `--model`, `model` setting. `opusplan` uses Opus in plan mode, Sonnet when
  executing.
- **Effort level** (`/effort`, frontmatter `effort`) and extended thinking.
  On recent models changing effort keeps the cache; on others it is a full
  cache miss and the UI asks for confirmation while the cache is warm.
- **Fast mode**: a faster, pricier serving tier toggled per session; falls
  back to standard speed on rate limits.
- **Advisor tool**: an opt-in tool to escalate hard decisions to a stronger
  model (`/advisor`).
- **1M context** variants (`[1m]` suffix) on supported models.
- **Automatic model fallback**: on some models, a request flagged by a safety
  classifier in a category with a fallback model is re-run on that model and
  the session continues there.
- A small/fast model (Haiku-class) is used for side requests such as session
  titles.

### Prompt caching (a central design constraint)
- Request layers, ordered by volatility: (1) system prompt + tool
  definitions, (2) project context (CLAUDE.md, auto memory, unscoped rules),
  (3) conversation. Only appends happen on normal turns.
- Things that would mutate earlier layers are instead *appended* as messages:
  plan-mode instructions, skill bodies, output-style switches, file-changed
  notices. Editing CLAUDE.md mid-session therefore does **not** apply until
  `/clear`, `/compact`, or restart.
- Cache-busting actions: model switch, effort change (older models), enabling
  fast mode (header is part of cache key, once per conversation), adding/
  removing tools when tool search is off, whole-tool deny rules (when tool
  search is off), compaction, image eviction, version upgrade.
- MCP tools are deferred via tool search by default, so the tool list is
  frozen from the first request; late-connecting servers add deferred tools.
- TTL: 5 min or 1 h. Subscription main conversation defaults to 1 h; other
  requests (subagents, compaction, titles) default to 5 min. Overrides:
  `promptCacheTtl`, `subagentPromptCacheTtl`, env vars.
- Workflow fan-outs hold same-prefix agents up to 5 s so they hit the first
  agent's cache. Forked subagents share the parent prefix; fresh subagents do
  not.
- `/usage` shows hit ratio and the likely cause of the last miss.

### Providers
Anthropic API, Amazon Bedrock, Google Cloud Agent Platform (Vertex), Microsoft
Foundry, Claude Platform on AWS, LLM gateways via `ANTHROPIC_BASE_URL`. Docs
publish a gateway compatibility guide (which headers/markers must pass).

## Harness design takeaways
- **T1** — Order the request by volatility and make every mid-session change
  an *append*; it turns prompt caching from an optimization into an
  architectural invariant.
- **T2** — Steering by queued messages read between tool calls gives
  low-friction course correction without aborting work.
- **T3** — Let lifecycle hooks veto "stop" to implement goal-seeking loops
  outside the model.
- **T4** — Record tool calls before execution so a crash can be resumed
  safely ("check before re-running").
- **T5** — Guard compaction against thrash loops with an attempt cap.
- **T6** — Separate TTL policy per request class (interactive vs. helper).
- **T7** — Warn the user before cache-busting actions while the cache is
  warm, not after.

## Open questions
- Exact concurrency rules for parallel tool execution (which tools are
  considered concurrency-safe) are not documented. (uncertain)
- Retry/backoff policy on API errors and overloaded responses is only
  partially documented.
