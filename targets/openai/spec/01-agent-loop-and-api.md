# 01 — Agent loop and model API  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

The desktop app never calls the model itself. It launches the bundled Codex CLI in its
`codex app-server` mode and talks to it over JSON-RPC (stdio by default). Inside the CLI, each
thread is a headless session. The session takes submissions from an input queue and emits a
stream of typed events, and the app-server layer turns those events into versioned JSON-RPC
notifications for whichever client is attached. A turn is a sampling loop against the OpenAI
Responses API. It prefers a persistent WebSocket with incremental continuation and falls back to
HTTPS server-sent events (SSE). The loop runs until the model stops asking for follow-up, the
token budget forces compaction, or the user interrupts. The rest of this spec covers the turn
lifecycle, the transport, retries, token and rate-limit accounting, steering and interruption,
and the protocol surface the UI drives.

## Diagram

```
 UI client (desktop webview / TUI / IDE)         host process                 Codex core
 ───────────────────────────────────────         ────────────                 ──────────
 submit message ──(client message id)──▶ app-server launcher ──JSON-RPC──▶ app-server
   turn/interrupt | turn/steer | turn/start      (stdio; also unix socket,          │
   thread/start | resume | fork | queue          in-process, ws/wss)                ▼
        ▲                                                                 thread manager
        │ notifications: turn/*, item/*, token usage, rate limits,          │ submission ─▶ session loop
        │ model reroute, error, server-initiated approval requests          │ event      ◀── session
        └───────────────────────────────────────────────────────────────────┘
                                                                            ▼
            ┌──────────────────────────── one turn ────────────────────────────────┐
            │ hooks + record user input → resolve skills/plugins → build tool set   │
            │ → pre-sampling compaction check → assemble prompt                     │
            │ loop {                                                                │
            │   sample (stream from model client)                                   │
            │   collect usage; decide follow-up; compact if over the token limit    │
            │   drain in-flight tool calls                                          │
            │   exit when no model follow-up and no pending user input              │
            │ }                                                                     │
            └──────────────────────────────────┬───────────────────────────────────┘
                                               ▼
                        model client
                        ├─ WebSocket: "response.create" frames, prewarm,
                        │             incremental continuation, "response.interrupt"
                        └─ HTTPS: POST {base}/responses (text/event-stream)
                           base = ChatGPT backend (ChatGPT sign-in) | api.openai.com/v1 (API key)
                        retries: request layer + stream layer + exponential backoff
                        model catalog: GET /models, local cache, ETag invalidation
```

## Mechanics

### Process topology

- The desktop host launches `codex app-server` and passes config overrides as `-c key=value`
  arguments. Logging defaults to JSON output at warn level.
- On WSL the host turns on an extra feature through a `-c` override before it starts the server.
- The app-server can also run as a long-lived daemon. There is a version probe and a recovery
  path for turns that a daemon restart cut short (see Interruption).
- Transports: stdio, unix socket, in-process, and WebSocket. Remote WebSocket connections that
  use auth tokens must be `wss://` or loopback.
- Every JSON-RPC request is traced with its method and request id, and W3C trace context is
  propagated.

### Submissions and events

- The core takes a single submission enum. Its main groups:

| Group | Purpose |
|---|---|
| Turn input / settings | Start or feed a turn. Thread-level and turn-level settings are separate submissions, not fields on the input. |
| Interrupt | Plain interrupt, plus a variant that aborts only when no user input is queued. |
| Approval replies | Exec approval, patch approval, permission requests, MCP elicitation, answers to user-input questions, dynamic tool results. |
| Maintenance | Refresh MCP servers, reload user config, set the thread's memory mode, clean up background terminals. |
| Lifecycle | Recover a turn after a crash; suspend a turn and shut down. |
| Other | Review mode, user-run shell commands, inter-agent messages, realtime voice session control. |

- Approval decisions go beyond yes/no. There is approve, approve for the session, approve with
  an amendment to the command policy, approve with an MCP policy amendment, allow or deny with a
  network-policy change, and abort.
- The event enum covers turn lifecycle (started, complete, aborted, shutdown), streaming deltas
  (agent message, plan, reasoning summary, raw reasoning, section breaks), item start and finish,
  token counts, model reroute and verification, moderation and safety buffering, context
  compaction and rollback, tool activity (exec begin, output and end; terminal interaction; MCP
  calls; web search; image generation; patch apply; turn diff; plan update), approval and
  elicitation requests, hooks, multi-agent activity, queue changes, MCP startup progress,
  environment connect and disconnect, review mode, deprecation notices, and non-fatal stream
  errors.
- Fatal errors carry a typed error-info enum. It distinguishes context overflow and per-session
  budget exhaustion; usage, rate and quota limits (including "not included in your plan");
  overload and 5xx failures; HTTP and stream connection failures; auth (401) and malformed or
  invalid-prompt requests; sandbox failures; exhausted retries; and several distinct
  policy-violation kinds. The app-server version adds stream disconnected, too many failed attempts, turn not
  steerable, flex tier unavailable, too many denials, and rollback failed.

### Turn pipeline

- The session code is split into many small modules: turn context, turn input, input queue,
  suspension, per-step settings, reasoning effort, token budget, context window, retained
  context, a world-state snapshot, time reminders, MCP and startup prewarm, daemon recovery,
  extension interruption, injection, and review. Turns come in four kinds: regular, compact,
  review, and user shell.
- Phases run in this order and each one gets a trace span:
  1. run hooks and record the inputs
  2. prepare tool recommendations and extension-provided input items
  3. resolve skills and plugins, then load the discoverable tools
  4. pre-sampling compaction
  5. build the prompt
  6. sample (stream request, receive)
  7. drain in-flight tool calls
  8. collect post-sampling state and prepare the next request's input
  9. flush the rollout to disk
- After sampling, the loop looks at estimated and actual token counts, the auto-compact limit
  and its scope, whether the full context window was reached, whether the model needs a
  follow-up, and whether user input is pending. The loop appears to continue while the model
  wants a follow-up or input is pending. When the token limit is hit mid-turn it likely compacts
  and keeps going instead of failing.
- If a turn has to wait for the user (an approval or a question), the core can post a short
  canned assistant status that sends the user back to the app. This is likely meant for headless
  or remote surfaces.
- The session tracks the requested model and a fallback model. A thread can opt in to fallback
  at start, and a server-side reroute reaches the UI as `model/rerouted`.

### Responses API request

- Request body: the core trio is `model`, `instructions` and `input`. Tooling is described by
  `tools`, `tool_choice` and `parallel_tool_calls`. Reasoning settings go in `reasoning`, and
  `text` carries verbosity plus an optional strict JSON-schema format. Streaming uses `stream`
  with `stream_options`. Also sent: `store`, `include`, `service_tier`, `client_metadata`,
  `prompt_cache_key`, a reasoning-summary delivery option, an access-program field, and a
  Codex-specific output-schema field.
- WebSocket frames have type `response.create` and the same body, plus `previous_response_id`
  and trace headers carried as body fields.
- `include` asks for `reasoning.encrypted_content`. The client therefore appears to run
  stateless (`store: false`, likely always under ChatGPT sign-in) and to carry encrypted
  reasoning between requests itself.
- The effort ladder runs from `none` and `minimal` through `low`/`medium`/`high` up to `xhigh`,
  with further tiers above that which appear to be reserved for newer models. Summary modes: `auto`, `concise`,
  `detailed`, `none`.

| config.toml key | Effect |
|---|---|
| `model_reasoning_effort` | default effort |
| `plan_mode_reasoning_effort` | effort used in plan mode |
| `model_reasoning_summary` | summary mode |
| `model_verbosity` | text verbosity |
| `service_tier` | priority/flex tier |

- Output item types the client parses: `message` (with a `phase` of commentary or
  final_answer), `reasoning` (summary text, raw text, encrypted content), `function_call`
  (namespaced, and possibly with encrypted arguments), `function_call_output`, custom tool calls
  and their outputs, local shell calls, tool search calls and outputs, web search, image
  generation, compaction items, and a configuration-update item.
- Stream events the client handles internally: output item added and done, text delta,
  reasoning summary deltas and parts, raw reasoning delta, tool input delta, rate-limit
  snapshot, models ETag, which model actually served the request, whether reasoning was
  included, model verifications, moderation metadata, and completed.
- Distinct errors exist for a stream that closes before `response.completed`, unparseable SSE
  events, idle timeouts, and incomplete responses. If the model stops early because of a content
  filter, the user sees a disconnect message that names that reason.

### Headers and routing

- Requests carry correlation ids (client request id, session, thread, turn, window, parent
  thread, sub-agent kind, installation id) and a routing hint. They can also carry an
  attestation header, beta-feature flags, a timing-metrics opt-in, and the opaque turn-state
  token described below.
- The client reads these response headers: the model that served the request, whether
  reasoning was included, the request id, a turn-state token, a models-catalog ETag, moderation
  and verification metadata, and a retry-model hint. The turn-state token appears to be an
  opaque value that is echoed back on later requests in the same turn, likely for sticky
  routing or cache affinity. The ETag likely makes the client refresh its local model catalog.
- Rate-limit headers are built from a per-limit prefix plus suffixes. For a primary and a
  secondary window they give used percent, window length in minutes, and reset time, plus a
  limit name. Credit headers report whether the account has credits, whether usage is unlimited,
  and an optional promo message.
- Base URLs: a ChatGPT backend path when signed in with ChatGPT, and `api.openai.com/v1` with
  an API key (`OPENAI_BASE_URL` overrides it). Endpoints: `/responses`, `/models`, and a realtime
  calls endpoint.
- Provider entries (`[model_providers.*]` in config.toml) cover addressing (`base_url`, a
  model-catalog URL, `query_params`, `wire_api`), credentials (`env_key`,
  `requires_openai_auth`, plus gateway OAuth, AWS and bearer-token options), resilience
  (`stream_idle_timeout_ms`, `request_max_retries`, `stream_max_retries`, a WebSocket connect
  timeout) and capability flags (WebSocket support, standalone web search).

### WebSocket transport

- A request is sent in one of three modes: no previous request, full, or incremental. The
  connection is prewarmed when the session starts, and the desktop asks the app-server to
  prewarm thread start as well.
- For incremental continuation the client keeps the last response id and request id. Before it
  sends only the new items plus `previous_response_id`, it checks that the connection reuse
  properties, the request length, and the earlier items all match what it sent last time. A
  failed check means a full request instead. In effect this is a client-side prefix-diff
  protocol.
- Interrupting sends a `response.interrupt` frame with the response id and a flag that says
  whether to discard partial items.
- Failure modes: the server closes before completion, idle timeouts on receive and on send, an
  unexpected binary frame, or the consumer going away. On repeated failure the client falls back
  to HTTPS for the session, and a diagnostics helper points the user at network-path causes:
  DNS, proxies and VPNs, firewalls, custom certificate authorities, or policies that block
  WebSockets.
- If auth fails mid-stream, a recovery path runs. Its outcomes are succeeded, permanent
  failure, or transient failure, and it surfaces to the UI as auth-recovery started and
  completed. The client also catches the case where the account changed while a request was
  being built.

### Retries and backoff

- Retries happen at three layers that share one telemetry schema (attempt, delay, layer,
  operation): HTTP request retries, stream-level reconnects, and an exponential-backoff utility
  underneath.
- `request_max_retries`, `stream_max_retries`, and `stream_idle_timeout_ms` are set per provider,
  and there is also a WebSocket connect timeout. When the limit runs out, the user gets a
  terminal error with the last HTTP status.
- While retrying, the user sees non-fatal "reconnecting" or "waiting for network" status. These
  are sent as stream-error events, not as fatal errors.
- The client honors server hints: `Retry-After`, and a "try again in N s/ms" phrase that it pulls
  out of error text with a regex. These likely replace the computed backoff for 429 responses.
- Outcomes are bucketed for analytics: timeouts (transport vs connection), network and stream
  errors, failures to build the request, oversized responses, other HTTP statuses, retryable API
  errors, overload, rate and quota limits, plan-excluded usage, invalid requests, policy errors,
  context overflow, and exhausted retries.

### Rate limits and credits

- A rate-limit snapshot holds one entry per window (used percent, window length in seconds,
  seconds until reset), a remaining amount, a credits snapshot (has credits, unlimited, balance),
  and a "reached" type. Reached types: plain rate limit, or credits depleted / usage limit
  reached for a workspace owner or member.
- The snapshot is pushed to the UI as `account/rateLimits/updated`. Clients can also fetch it on
  demand, and there is an RPC that spends a rate-limit-reset credit.

### Token accounting and context window

- One usage record holds input, cached input, cache-write input, output, reasoning output, and
  total tokens. The client keeps it as both a running total and a last-request value, next to
  the model's context window. Token-count events carry this record together with the current
  rate limits.
- Each request also emits the same numbers as OpenTelemetry GenAI semantic-convention
  attributes, plus reasoning effort.
- The app-server pushes `thread/tokenUsage/updated` with total, last, and context window, and
  replays it when a thread is resumed.
- Compaction-related config.toml keys: the window size is `model_context_window`; the
  automatic trigger is `model_auto_compact_token_limit` (with a limit-scope option), and a post-turn compaction threshold
  in percent. A token-budget feature adds a reminder threshold, a reminder template, and a
  fallback compaction prompt with a buffer. When the context is exhausted the user is advised
  to begin a fresh thread or trim older history before retrying. Compaction itself is covered in a sibling spec.
- Separate workers report per-turn, per-thread, and per-task usage and cost back to the backend.

### Interruption, steering, and queueing

- `turn/interrupt` (thread id, turn id) maps to the interrupt submission and errors if no turn
  is active.
- After an interrupt, the next model input starts with a developer-role note inside a
  `<turn_aborted>` tag. The note says the user stopped the previous turn on purpose, that
  background exec processes may still be running, and that aborted tools may have partly
  executed. A second wording covers interruptions that were not started by the user.
- `turn/steer` pushes extra user input into the running turn, guarded by an expected turn id. It
  is refused when no turn is running, during review or compaction turns, and when the active
  turn uses a different output schema. A refusal comes back as a typed not-steerable error.
  Steer can also attach client metadata and additional context. Steered input appears to go
  into the pending-input queue, which makes the loop sample again instead of ending.
- The desktop shows a steered message optimistically as a pending item keyed by its client
  message id and removes it once the submission is confirmed. Returning a client-side tool
  result does not go through steer. It is sent as a `turn/start` with empty input and a tool
  output.
- Each thread has a persistent queue of next-turn messages with add, list, update, delete,
  reorder, and start operations, and a change notification.
- The desktop treats all turn- or thread-creating RPCs as non-idempotent deliveries (thread
  start and fork, item injection, queue add, add user message, turn start, steer). If delivery
  fails, the result is "outcome unknown" rather than an automatic retry, and unconfirmed
  submissions are reconciled by client message id.
- Crash and restart recovery: a recover-turn submission resumes the work and shows the user a
  "resuming interrupted work" state. The model gets a note saying the server restarted, that it
  should carry on from the saved conversation, and that it should check current state before
  redoing actions that may already have happened. A recovered continuation cannot be steered.
- Suspend-and-shutdown saves thread settings before the process exits.
- If the model wants to steer a background agent task during a realtime voice session, the
  steer message carries a short marker saying what it is for.

### Model catalog

- The model catalog is fetched from the server and cached locally in a JSON file. The cache
  records when it was fetched, the client version, and the provider/auth identity, and the
  client discards it if the identity does not match. A static catalog is used where remote
  discovery is unavailable. An app-server worker refreshes the catalog, likely when it sees a
  new ETag.
- Each model entry configures the harness for that model: display name, default and supported
  reasoning levels, shell type, visibility, API support, priority, speed and service tiers,
  access programs, upgrade target, per-model messages, whether to include skills, plugins, and
  apps usage instructions, reasoning-summary support and default, verbosity support and default,
  which flavor of `apply_patch` and web search tool to use, base instructions, personality
  variants, tool namespaces, and bundled MCP servers. Prompt, tool flavor, and effort ladder are
  all driven by the server, per model.
- The catalog lists released models such as `gpt-5.1-codex-max` as well as several newer
  frontier models that have not been released.

### App-server protocol surface (v2) used by the loop

| Direction | Methods |
|---|---|
| Client → server | Threads: `thread/start` and its siblings for resuming, forking, reading and listing, archiving and restoring, rolling back or reverting, starting compaction, injecting items, updating settings, and unsubscribing. Turns: `turn/start`, `turn/interrupt`, `turn/steer`, plus a turn-settings update. Also `review/start`, `model/list`, a provider-capability read, `account/rateLimits/read`, an account-usage read, `command/exec` (with write, resize and terminate), process control, collaboration-mode listing, realtime, and fuzzy file search |
| Server → client | command-execution approval, file-change approval, tool user-input request, permissions approval, dynamic tool call, MCP elicitation, ChatGPT token refresh, attestation; older-style patch/exec approval |
| Notifications | `thread/tokenUsage/updated`; thread started and status changed; `turn/completed`, `turn/started`, diff and plan updates, moderation metadata; `item/started`, `item/completed`; agent-message, plan, and reasoning deltas; command output and terminal interaction; file-change output and patch updates; MCP progress; auto-approval review started and completed; raw response item; server request resolved; hooks; `model/rerouted`, model verification, safety buffering; `account/rateLimits/updated`; `error`, `warning`, deprecation notice |

- Thread item kinds: user message, hook prompt, agent message (with phase), memory citation,
  function call output, reasoning, command execution, file change, MCP tool call, dynamic tool
  call, collaborative agent tool call, web search, image view, sleep, image generation, entered
  review mode, context compaction. Turn status is one of completed, interrupted, failed, or in
  progress.
- The webview keeps a fixed table that tags each notification method with a boolean. It likely
  marks whether the notification is persisted or replayed. The webview treats JSON-RPC
  method-not-found errors as a sign of an older server and degrades gracefully.

## Harness design takeaways

- **T1** — Build the agent as a headless core with an explicit submission queue and typed event
  stream, and put a versioned JSON-RPC server in front of it. Every UI (desktop, TUI, IDE,
  remote) is then a thin client, and one binary serves stdio, sockets, and WebSocket.
- **T2** — Write the turn as a named sequence of phases (hooks, input recording, tool discovery,
  pre-sampling compaction, prompt build, sampling, tool drain, post-sampling state) with a trace
  span around each. Whether to loop again comes down to one boolean: the model asked for more,
  or the user has queued input.
- **T3** — Keep three separate mechanisms: steer (inject into the running turn through a
  pending-input queue), queue (saved messages for the next turn), and interrupt. Where steering
  is unsafe (review, compaction, a different output schema), refuse it with a typed error.
- **T4** — After an interrupt, tell the model in a short tagged developer note that background
  processes may still be running and tools may have partly executed. Do the same after crash
  recovery, and tell the model to verify current state before repeating any action.
- **T5** — Prefer a long-lived WebSocket where follow-up requests carry just the new items and
  the prior response id. Before relying on that, confirm the locally held prefix is unchanged.
  If it differs, resend everything, and if the socket keeps failing, drop to plain HTTPS
  streaming. Open the socket early, when the session starts.
- **T6** — Interrupt at the transport level too, with an explicit cancel frame that says whether
  to keep partial output, instead of only dropping the stream. The server stops generating, and
  the client decides what to keep.
- **T7** — Use layered retries (HTTP request, stream reconnect, transport fallback) that share
  one telemetry schema. Make the limits per-provider config, and honor server hints like
  `Retry-After` or "try again in N seconds".
- **T8** — Report transient trouble as a non-fatal "reconnecting" signal, kept apart from
  terminal errors that carry a typed error enum. The UI can keep showing progress, and outcome
  analytics get clean categories.
- **T9** — Treat RPCs that create turns as non-idempotent. Stamp each with a client-generated
  message id and keep a list of sends not yet acknowledged. If the connection drops, mark the
  result as unknown and check it against the server's state. Retrying blindly creates duplicate
  turns.
- **T10** — Keep token accounting in a single canonical record: input, cached, cache-write,
  output, reasoning, and total. Hold both a cumulative and a most-recent-request copy next to
  the context window size, publish it after each sample, re-send it when a thread resumes, and
  mirror it into OpenTelemetry GenAI attributes.
- **T11** — Read rate-limit and credit state from the headers of every response (per-window
  used percent, window length, reset time, limit name, credits) and push it to the UI, so users
  see their quota before they hit a 429.
- **T12** — Let a server-hosted catalog decide how the harness behaves for each model: base
  instructions, the effort ladder, summary and verbosity defaults, which patch and search tool
  variants to expose, and service tiers. Cache it locally with an identity check and refresh it
  when the ETag changes.
- **T13** — Never swap models silently. Reroutes, fallbacks, and safety buffering should reach
  the UI as events of their own.
- **T14** — Tag every model request with correlation ids: session, thread, turn, and window ids,
  the parent thread, the sub-agent kind, a client request id, and an opaque turn-state token the
  server hands out and the client echoes back. The backend can then rebuild multi-agent trees
  and retry chains and keep traffic cache-affine.
- **T15** — Have each assistant message state its phase (interim commentary or final answer).
  The UI and the loop can then handle progress narration and the real answer separately without
  guessing.

## Open questions

1. Default values for the request and stream retry limits, the stream idle timeout, and the
   backoff base and jitter. These were not visible in static strings.
2. What the opaque turn-state token is for (routing stickiness, or a key into a server-side
   cache), and whether ChatGPT-signed-in sessions ever enable response storage.
3. What `prompt_cache_key` is computed from: the thread id or a fingerprint of the
   conversation.
4. How the access-program request field and the lightweight-responses request option change
   server handling.
5. What the per-notification boolean in the webview's method table means: persistence and
   replay, or something like subscription requirements.
6. Whether the desktop uses the "interrupt only if nothing is queued" variant (for example from
   automations). No caller was found in the UI code.
7. Whether tool results are drained before or after the post-sampling auto-compaction check.
8. The full set of turn-start parameters. Only some are known: expected turn id, tool output,
   additional context, client metadata, collaboration mode, multi-agent mode, and an
   access-program option.
