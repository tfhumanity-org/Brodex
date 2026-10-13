# 03 — Tool inventory exposed to the model  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

The Codex agent core builds a fresh tool list for every turn and sends it with
the Responses API request. Which tools appear, and in what shape, is decided
mostly by per-model metadata shipped inside the CLI (patch tool flavour, shell
flavour, search tool flavour, output truncation policy, parallel-call support,
multi-agent version, experimental tool opt-ins). The defining design choice in
this release is that newer frontier models are served a "code mode" surface:
the model gets one primary freeform tool, `exec`, which runs JavaScript in a
sandboxed V8 isolate, and every other tool (shell, `apply_patch`, MCP tools,
agents, image generation) is called as an async function from inside that
script. The released `gpt-5.5` model instead uses classic direct function
calling. Both paths converge on the same core tool router, sandbox, and
approval machinery.

## Diagram

```
                 Responses API request (tools[], parallel_tool_calls)
                                   |
          +------------------------+-------------------------+
          | code-mode surface (newer frontier models)        | direct surface (gpt-5.5)
          v                                                  v
   freeform `exec` (raw JS) + `wait`                exec_command, write_stdin,
   (+ a few tool groups kept direct-only)           apply_patch (freeform), update_plan,
          |                                         view_image, request_user_input,
          | local IPC (versioned protobuf protocol)  tool_search, spawn_agent ...,
          v                                         mcp__server__tool, hosted web_search
   code-execution host process (V8)                          |
     session -> exec cell -> isolate                         |
     exposes the tool table, output helpers,                 |
       a key/value store, and a yield primitive              |
          |  nested tool invocation                          |
          +-------------------> core tool router <-----------+
                                       |
     +-----------+-----------+---------+---------+------------+------------+
     v           v           v                   v            v            v
  unified exec  apply_patch  MCP client     multi-agent    extension   hosted (server-side)
  (PTY/pipes,   (grammar-    (prefixed      tools          tools       web_search,
   sandbox,     constrained, names,                        (search,    image generation
   approvals)   heredoc      resources)                    image gen,
                detection)                                 sleep, ...)
```

## Mechanics

### Model metadata drives the tool surface

Each model entry in the bundled catalog carries fields that select the tool
set. Observed values across the shipped catalog:

| Field | Value on current models |
|---|---|
| apply_patch type | freeform (grammar-constrained custom tool) |
| web search type | text and image |
| truncation policy | token-based, limit 10000 |
| parallel tool calls | supported |
| tool mode | code-mode-only on newer frontier models; unset (direct tools) on `gpt-5.5` |
| shell type | one-shot shell command style |
| multi-agent version | v2 on most newer models, v1 on some, none on `gpt-5.5` |
| context window | 272k typical; some newer entries larger |
| search tool support | enabled everywhere (gates `tool_search`) |

- Some newer models opt into experimental tools (an async user-message tool and
  a clock/sleep tool) via the catalog.
- Many sub-features (streaming patch events, line-ending preservation, stdin
  approvals, permission-request tool, code mode itself, host prewarming, cached
  vs live web search, deferred tool execution) are individually feature-gated.
- If a model lacks code-mode metadata while code mode is forced on, the CLI
  warns that performance may degrade and suggests turning the code-mode
  features off.
- When the code-execution host is unavailable there are two policies: fall back
  to the direct tool surface, or fail closed.

### `exec` — the code-mode tool

- Freeform tool: the model emits raw JavaScript source, not JSON arguments.
- An optional first-line comment pragma may set `yield_time_ms` and
  `max_output_tokens`; it is strictly validated (only those two keys,
  non-negative safe integers, and real source must follow on later lines).
- Each call gets a brand-new V8 isolate and the source is evaluated as an async
  ES module. There is no Node runtime, no file system, no network, and no
  console. Promises still pending when the module finishes are dropped.
  `max_output_tokens` defaults to 10000; the yield default comes from config.
- Globals available to scripts:

| Global | Purpose |
|---|---|
| `tools.<name>(input)` | call any tool; accepts string or object, returns object or string |
| `ALL_TOOLS` | array of `{name, description}` for every callable tool |
| `text(v)`, `image(...)`, `audio(...)` | append content items to the tool output |
| `generatedImage(...)` | return an image produced by the image-generation tool |
| `store(key, v)` / `load(key)` | persist plain serializable values across `exec` calls in a session |
| `notify(v)` | push an extra tool output to the model immediately |
| `yield_control()` | hand whatever output has built up so far back to the model; the script keeps executing |
| `setTimeout` / `clearTimeout`, `exit()` | timers and early termination |

- Deferred tools may be left out of the prompt description but remain callable;
  the model is told to find them by filtering `ALL_TOOLS` itself. Tool discovery
  therefore happens in JavaScript rather than through an extra tool round trip.
- Tool definitions are presented to the model as TypeScript declarations (a
  declared `tools` object with typed async methods), preceded by one shared
  block of MCP content and result types. A JSON Schema to TypeScript converter
  handles unions, intersections, `const`, tuple items and open-ended records.
- Companion `wait` tool (JSON arguments: cell id, yield time default 10000 ms,
  max tokens default 10000, terminate flag). It is only meant for cells that
  reported they are still running, and it returns just the output produced
  since the previous yield, or the final result.
- Results end with a status footer (terminated, failed, or wall time in
  seconds).
- The newer-model instructions push parallelism into the script: independent
  reads and searches should be issued together and awaited with
  `Promise.allSettled`, while mutations and approval-bearing actions stay
  sequential.

### Code-execution host process

- A separate helper process embeds V8 and speaks a versioned protobuf protocol
  to the CLI over gRPC or stdio. The handshake negotiates protocol version and
  required capabilities.
- Message types cover session readiness, execution start, wait completion, cell
  closure, delegated tool invocations and notifications (with cancellation),
  yield observations, and tool-call completion.
- Limits: per-session execution limits, a maximum heap size, an IPC frame size
  cap, and a byte cap on tool invocation payloads. The core refuses a host that
  cannot enforce the session limits it requests.
- Each exec cell is an actor with its own lifecycle (create, observe, terminate,
  close); duplicate or missing cells are hard errors.
- `store()` accepts only plain serializable objects; module imports are
  rejected; `image()` accepts only MCP image blocks.
- The core prewarms the host at startup and can reconnect to it. A config
  switch to disable in-process fallback appears to imply V8 can also run inside
  the CLI process if the external host fails.

### Unified exec: `exec_command` and `write_stdin`

`exec_command` parameters:

| Parameter | Meaning |
|---|---|
| `cmd` | command string |
| `workdir` | defaults to the turn's cwd |
| `shell` | defaults to the user's shell |
| `login` | login/interactive shell semantics, default true |
| `tty` | PTY vs plain pipes |
| `yield_time_ms` | default 10000; effective range 250–30000 |
| `max_output_tokens` | default 10000; may be capped by policy |
| `sandbox_permissions` | use default / with additional permissions / require escalation |
| `additional_permissions` | network on/off, extra filesystem read and write paths |
| `justification` | the approval question shown to the user when escalating |
| `prefix_rule` | a reusable command prefix to approve (e.g. `["git","pull"]`) |
| environment id | target environment for remote/multi-environment setups |

- Returns either completed output or a session id for continued interaction.
  Structured result fields: output (possibly truncated), exit code, session id,
  wall time, chunk id, and the approximate original token count before
  truncation. The text rendering also reports total output lines and timeouts.
- On Windows the description adds safety rules: keep one shell for the whole
  command, double-check targets of recursive deletes, and launch background
  processes hidden.
- `write_stdin` takes a session id and characters to send; sending nothing is a
  poll. Writes that send characters yield after 250 ms by default, capped at
  30000 ms; a pure poll waits somewhere between 5000 and 300000 ms. Stdin writes can themselves require approval, and
  oversized input is refused as too large to review.
- A one-shot, non-PTY variant runs a command to completion with a timeout
  (default 10000 ms) and cannot be resumed; this likely backs the one-shot shell
  type when PTY sessions are disabled.
- There is a cap on concurrently open processes (warned about when reached),
  and on interrupt the model is reminded that background processes may still be
  alive.
- Rejections are phrased as corrective guidance for the model (e.g. a
  justification without an explicit permission mode is rejected; escalation is
  refused when `approval_policy` is `never`; additional permissions require the
  corresponding feature).
- Backends include a zsh-fork runner and direct spawn, shell environment
  snapshots, and automatic proxy environment injection when a managed network
  proxy is active.
- A separate `request_permissions` tool lets the model ask for extra filesystem
  or network access; the user may grant a subset, scoped to the rest of the turn
  or the whole session, and grants apply automatically to later shell commands.

### `apply_patch`

- Sent as a Responses API custom tool with a grammar format, so decoding is
  constrained. The description tells the model not to wrap the patch in JSON.
- Grammar structure: a begin marker, one or more hunks, an end marker. Hunk
  kinds are add file (all lines prefixed `+`), delete file, and update file. An
  update hunk may carry a move/rename target and a sequence of change blocks;
  each block has optional context header lines (a double at-sign marker,
  optionally followed by an anchor line) and lines prefixed with `+`, `-` or a
  space, with an optional end-of-file marker. A variant permits an environment
  id header right after the begin marker.
- A tree-sitter bash query recognises heredoc invocations of `apply_patch`
  (optionally preceded by a `cd ... &&`) issued through the shell, so the shell
  habit still routes to the patch engine. Older prompts used a JSON command
  array form.
- Results report the list of updated files or that nothing changed; malformed
  first/last lines are reported specifically. Sandbox outcomes distinguish
  writes blocked by a read-only sandbox, writes outside the project, and patches
  the user rejected. Line-ending preservation is configurable.

### Planning and UX tools

| Tool | Behaviour |
|---|---|
| `update_plan` | optional explanation plus a list of steps with status; only one step may be in progress; not allowed in Plan mode |
| `request_user_input` | 1–3 questions, each with a snake_case id, a header of at most 12 characters, the question, and 2–3 mutually exclusive options with the recommended one first; the client adds its own free-form "Other"; only available in certain modes |
| `view_image` | path plus detail level (resized high by default, or original); returns a data URL; hidden when the model has no image input |
| context-remaining query | returns tokens left in the window |
| sleep (clock group) | bounded pause that ends early when new user input arrives |
| new-context | starts a fresh context window without touching environment state; paired with notes/history tools |
| async user message | experimental; must not duplicate the final answer |
| wait-for-environment | blocks until a remote environment is ready |

Test-only synchronization tools also exist in the binary.

### Discovery: `tool_search` and plugin install

- `tool_search` runs BM25 over the metadata of deferred tools and makes the
  matches available on the next model call. Parameters: `query` and `limit`.
  The model is told to use it, not MCP resource listing, for MCP tool discovery.
- `tool_search` calls and outputs are first-class Responses item types. Tools
  can be marked for deferred loading and grouped into namespaces with their own
  descriptions.
- Installed apps' MCP tools are either present already or lazy-loaded through
  `tool_search`.
- Two plugin tools list installable plugins and request an install. They may be
  used only when the user explicitly asks, only for recommended plugins, only
  from the root thread, and never in parallel with other tools.
- Several ranking strategies (lexical, fielded BM25, character n-gram, fusion)
  appear to exist for skill/plugin routing alongside the tool search.

### MCP bridging

- MCP tools are named `mcp__<server>__<tool>` (the same names appear on the JS
  `tools` object); config can opt specific servers out of prefixing.
- Resource tools: `list_mcp_resources`, `list_mcp_resource_templates`,
  `read_mcp_resource`; the model is nudged to prefer resources over web search.
- Per-server `config.toml` controls include `enabled_tools`, `disabled_tools`,
  per-tool approval mode, `tool_timeout_sec`, `startup_timeout_sec`, an output
  token limit, a parallel-call flag, and an input-schema size cap.
- Incoming schemas are sanitized: unsupported constructs, non-object roots and
  encrypted parameters are rejected or replaced with bundled parameters. Tool
  metadata is held to a byte budget, and excess is shed so large catalogs do not
  flood the context. Tool-name collisions can be made an error.
- MCP elicitation is supported. App-server clients can also register dynamic
  tools that the core forwards to them and that may be cancelled.

### Collaboration tools (summary; see the agents spec)

`spawn_agent` (task name, message or items, agent type, model, reasoning
effort, how many parent turns to fork), `send_message`, `followup_task`,
`send_input` (v1, with interrupt), `wait_agent`, `interrupt_agent`,
`list_agents`, `close_agent`, `resume_agent`. An extension adds a shared message
board for agents (channels, threads, posts, subscriptions). A depth cap tells a
too-deep agent to do the work itself.

### Web search and image generation

- The hosted `web_search` tool supports live vs indexed access, domain filters,
  approximate user location, context size and content types.
- A standalone extension search tool appears to offer a ChatGPT-style command
  set (query, image query, open/click/find in results, PDF page screenshots,
  and several vertical lookups such as weather or finance).
- An image-generation extension tool takes a prompt, a transparent-background
  flag, and either referenced image paths or a count (at most 5) of recent
  images. In code mode the model is told to raise the first call's yield time
  to 120 seconds and return the image through `generatedImage`.

### Output truncation and budgets

- Global truncation is 10000 tokens per tool output; `tool_output_token_limit`
  in config overrides it, and individual calls can set `max_output_tokens`
  (`wait` uses a max-tokens field).
- Truncated output carries inline elision markers (characters or tokens
  removed) and a warning header with the original token count and total
  output lines. The elision likely keeps head and tail.
- Images are validated (data URL format, byte cap) and a resize notice is
  attached when they are downscaled.

### Parallel calls and dispatch

- `parallel_tool_calls` is sent per request; models and individual MCP servers
  declare whether they support it.
- One dispatcher handles both direct calls and nested calls from code cells,
  recording the source of each call and timing for dispatch and handling.
- The `gpt-5.5` prompt routes parallelism through a single parallel wrapper
  tool and discourages chaining shell commands with separator echoes; the
  newer-model prompt uses `Promise.allSettled` inside `exec` instead. The
  `gpt-5.5` prompt also insists on `apply_patch` rather than shell redirection
  for manual edits.
- Hooks (PreToolUse, PermissionRequest, PostToolUse) can rewrite tool input;
  malformed rewrites are rejected.

## Harness design takeaways

- **T1** — Make code mode the default tool surface: a single freeform
  JavaScript tool that exposes every real tool as a callable global, keeping
  direct function tools as the fallback. The
  model can batch, parallelize and filter tool output in code instead of
  spending turns and context.
- **T2** — Present tool schemas as TypeScript declarations with one shared MCP
  types preamble. Models read TS signatures reliably, and shared types are not
  repeated per tool.
- **T3** — Fresh isolate per call with explicit persistence. State crosses
  calls only through a store/load of serializable values, giving clean failure
  semantics and no leaked handles while still supporting pipelines.
- **T4** — Yield/resume for long work. Long primitives (code cells, PTY
  sessions) return after a yield window with a handle; a follow-up call fetches
  only new output. Typical numbers: a 10-second default yield, shell yields
  bounded to 250–30000 ms, and idle polls lasting 5 to 300 seconds.
- **T5** — A single default output cap that each call may override: 10000 tokens by
  default, per-call `max_output_tokens`, and the original token count plus total
  line count in results, so the model can judge how much was cut and ask again
  with a tighter query.
- **T6** — Freeform plus grammar for patch editing. Constrained decoding keeps
  patches well-formed, an optional environment header supports multiple
  environments, and heredoc interception means the shell habit also works.
- **T7** — Deferred tools plus BM25 search. Rarely used and MCP tools are
  deferred and grouped into described namespaces; `tool_search` surfaces the
  hits on the following model request. Under code mode, filtering `ALL_TOOLS` in JS avoids the extra
  round trip.
- **T8** — Let each shell invocation carry its own escalation request: permission
  mode, extra filesystem/network grants, a user-facing justification, and a
  reusable approval prefix. Validation errors are phrased as corrective
  instructions to the model.
- **T9** — Capabilities come from model metadata, not code branches. A catalog
  decides tool mode, the patch/shell/search flavours, output limits, whether
  calls may run in parallel, which agent protocol and which experimental tools
  apply, enabling per-model surfaces and
  safe rollouts.
- **T10** — Namespaced tools plus operator policy. Namespace exclusion, direct-only
  namespaces, collision errors and per-MCP enable/disable/approval/output
  limits give operators coarse and fine control; pre-tool hooks may rewrite
  input.
- **T11** — Schema sanitation plus a metadata budget. Repair or reject
  unsupported JSON Schema, cap schema size per server, and drop excess tool
  description bytes once a budget is exceeded so large MCP catalogs do not consume the window.
- **T12** — Small, tightly specified UX tools: bounded user questions with
  recommended-first options, a plan with exactly one active step, a
  context-remaining query, and a sleep that wakes on new input, each hard
  validated and mode gated.
- **T13** — Give the model tools to manage its own context window. Notes, history and new-context
  tools, reminders at a 6144-token threshold, and an auto-compact fallback
  buffer of 16384 tokens let the model checkpoint itself and start fresh rather
  than relying only on server-side summarization.
- **T14** — Parallelism guidance tied to the surface: a single wrapper in
  direct mode, `Promise.allSettled` in code mode, with mutations and approvals
  kept sequential; any tool that is unsafe to run alongside others states that
  restriction in its own description.
- **T15** — Rich, typed tool-call telemetry. Each dispatch records its source
  (direct vs code mode), a runtime call id, dispatch and handler durations and
  shed metadata bytes, which helps tune budgets and spot slow nested calls.

## Open questions

- The default code-mode yield time, the allowed range of the `tool_search`
  limit, and the sleep tool's bounds are not visible statically.
- The exact truncation algorithm (head/tail split, character vs token fallback).
- The maximum number of concurrently open unified exec processes.
- Which tool groups stay direct-only by default under code mode (agents,
  planning and user-input tools are plausible candidates).
- Default heap size and per-session execution limits of the code-execution
  host.
- Whether the notes/history tools are actually enabled on any shipped model;
  the related budget setting appears disabled, suggesting a dark launch.
- For a given provider, whether the standalone search tool supersedes the
  hosted `web_search` or both are offered.
- How tool names with dots or hyphens (e.g. from MCP servers) are normalized
  into JS identifiers.
