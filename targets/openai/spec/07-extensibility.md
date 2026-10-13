# 07 — Extensibility: MCP, plugins, skills, connectors, hooks, MCP Apps  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

Codex extends itself through four stacked mechanisms that all end up in the same
core agent runtime shared by the CLI and the desktop app. **MCP servers** are the
only way to add new callable tools. **Skills** are folders of instructions the
model loads on demand. **Plugins** are installable bundles that package skills,
MCP servers, connectors, hooks and display metadata, distributed through
**marketplaces**. **Apps (connectors)** are ChatGPT integrations exposed to the
agent as tools of one virtual MCP server, usually kept out of the prompt until
the model finds them with tool search. Around these sit three supporting
channels: lifecycle **hooks** (an event set compatible with Claude Code's),
**elicitation** (servers asking the user for structured input mid-call), and
**MCP Apps** (tools that render HTML widgets in a sandboxed frame).

## Diagram

```
  bundled marketplace (in app resources)          user / repo / remote marketplaces
            │ staged into CODEX_HOME at launch               │
            ▼                                                ▼
  ┌──────────────── desktop host ────────────────┐   ┌──── core runtime ─────────────────────────┐
  │ per-plugin config overrides (enable/disable) │──►│ plugin cache: <mkt>/<plugin>/<version>/   │
  │ host-tools socket  ◄── host-tools MCP proxy  │   │   plugin.json                             │
  │ built-in template-picker MCP server          │   │    ├─ skills/   → skill catalog (prompt)  │
  └──────────────────────────────────────────────┘   │    ├─ .mcp.json → MCP manager (stdio/HTTP,│
            ▲  app-server JSON-RPC                   │    │              OAuth, OS keyring)      │
            │  (marketplace, plugin, skills,         │    ├─ hooks     → hook engine             │
            │   mcpServer, hooks methods)            │    └─ .app.json → connectors server       │
            │                                        │ tool registry  mcp__<server>__<tool>      │
            │                                        │   always-loaded set | deferred set        │
            │                                        │                       └─► tool_search     │
            │                                        │ plugin-install suggestion tools           │
            │                                        └──────┬───────────────────────┬────────────┘
            │                                               │ elicitation           │ tool result
            │                                               ▼                       ▼ with ui:// ref
            └──────────────────────────── request panel (forms,       widget host: sandboxed
                                          file / gallery pickers)     iframe + ui/* bridge
```

## Mechanics

### Marketplaces and plugin packages

- The app ships a bundled marketplace (a `.agents/plugins/marketplace.json`
  listing local plugin folders, each with an installation policy, an
  authentication policy and a category). It contains 12 plugins covering host
  app tools, in-app and external browser control, desktop control, messaging,
  workflow recording, LaTeX, code review, research and visualization.
- Installation policy values are not-available, available, and installed by
  default; authentication can happen at install time or on first use. Plugins
  can also be blocked for reasons such as admin disablement, plan eligibility,
  or a required connector being unavailable.
- At launch the desktop host copies the bundled marketplace into a temporary
  area under `CODEX_HOME`, keyed by a bundle identifier, with separate bundled
  marketplaces per release channel.
- The core accepts several manifest dialects: Codex's own
  `.agents/plugins/marketplace.json` and `.codex-plugin/plugin.json`, plus the
  Claude (`.claude-plugin/...`) and Cursor (`.cursor-plugin/...`) equivalents.
  Inside a plugin it looks for `.mcp.json`, `.app.json`, `hooks/hooks.json` and
  `agents/openai.yaml`.
- A curated plugin catalog is synced from a git repository (shallow fetch with a
  lock file) or downloaded as an archive, with a consistency check on the
  fetched revision.
- Installed plugins live at `CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/`.
  There must be exactly one version directory, and the manifest's name must match
  the folder name. Remote installs leave a small marker file recording where the
  plugin's skills were placed.
- `plugin.json` carries the usual package metadata (name, version, description,
  author, homepage, repository, license, keywords), relative pointers to
  `skills/` and `.mcp.json`, inline hook definitions, and an `interface` block
  for the UI: display name, short and long descriptions, developer, category,
  capability tags (interactive / read / write), legal and website links,
  suggested starter prompts, brand color, icons, logos and screenshots. A
  vendor-namespaced `extensions` object can name an onboarding skill.
- The plugin `description` is written for the model, not for marketing. The
  bundled browser plugin's description lists its mention aliases and includes
  explicit "don't do X instead" rules.
- The host drives all of this over app-server JSON-RPC, with methods grouped
  under marketplace (add / remove / upgrade), plugin (list / read / install /
  uninstall / search / discovery / reconcile / read a plugin skill, plus a
  sharing family that publishes plugins to principals), skills (list / change
  notifications / config write / extra roots), hooks (list), and MCP servers
  (reload / OAuth login / call tool / elicitation / status).
- A migration importer detects Claude Code and Cursor setups (`CLAUDE.md`,
  `.cursorrules`, hook files, enabled-plugin lists) and offers to import them.

### MCP server configuration (`[mcp_servers.<name>]`)

- The server config table has about 30 fields. Transport: `command`, `args`,
  `env`, `env_vars` (an allow-list of names to pass through), `cwd`, or `url`
  with bearer-token and header options, including headers taken from env vars
  and a helper command that produces headers.
- Auth: OAuth (client id/secret, callback URL and port, explicit issuer), an
  identity-provider variant, ChatGPT-account auth, and an enterprise-managed
  mode. Tokens are stored in the OS keyring behind a file lock. OAuth discovery
  follows the standard authorization-server metadata and the RFC 9728
  protected-resource metadata.
- Lifecycle: startup timeout (seconds or milliseconds), tool timeout, a
  "required" flag, and a readiness mode. The readiness mode appears to choose
  between "connected" and "tool catalog received". There is also a global grace
  period for optional servers.
- Tool policy: `enabled`, `enabled_tools`, `disabled_tools`, a server-wide
  default approval mode, and per-tool overrides for approval mode and output
  token limit. Approval modes are auto, prompt, writes-only, and approve. A
  parallel-call capability flag and a cap on input-schema size also exist.
- Context placement: a per-server list says which tool pools the server's tools
  must be left out of. The two pools are the deferred (tool-search) pool and
  the code-mode scripting namespace. Core REPL-style servers opt out of both.
- Tools are named `mcp__<server>__<tool>`. Over-long qualified names are
  rejected, and name collisions can be configured to raise an error.
- On each call the core sends a metadata entry with thread and turn ids (plus
  vendor-prefixed aliases), so servers can tie calls to conversations. On the
  response side, vendor headers can choose a truncation policy for tool output
  and mark arguments as encrypted.
- Bundled `.mcp.json` files ship with `enabled: false` and `cwd` relative to
  the plugin root. The desktop host then turns each server on or off through
  dotted config overrides keyed by plugin, marketplace and server name.
- Bundled servers start through small shell launchers that find a Node runtime
  in priority order: an explicit env override, the runtime bundled in the app
  resources, a runtime next to the CLI, a cached downloaded runtime, and
  finally `node` on `PATH`.

### Host-tools plugin: MCP as a proxy to the desktop app

- This server declares no tools of its own. It forwards tool listing and tool
  calls as JSON-RPC to the desktop host over a local socket. Frames carry a
  4-byte little-endian length prefix and are capped at 8 MiB. A cancel message
  is sent when a call is aborted.
- Host tools carry an internal namespace field that the proxy strips. Host
  results (text, image and audio items plus a success flag) are converted into
  MCP content.
- Every call must include thread metadata or it fails. The caller is tagged as
  either the Codex agent or ChatGPT, depending on the launching client.
- The server's default approval mode is approve. Tools that create, message,
  fork or hand off threads, or update automations, are set to prompt instead.
  The tool timeout is 3600 s.

### Skills

- `SKILL.md` frontmatter holds `name`, `description` and an optional short
  description in `metadata`. Metadata files are capped at 1 MiB.
- An optional sidecar (`agents/openai.yaml`, or a JSON equivalent) adds UI
  details (display name, short description, icons, brand color, default
  prompt), an invocation policy, and declared dependencies on MCP servers
  (stdio or streamable HTTP) or env vars.
- Turning implicit invocation off keeps the skill usable when the user asks for
  it explicitly with `$skill-name`, but leaves it out of the automatic catalog.
- Discovery roots include the repository (`.agents/skills`), the user's
  `~/.agents/skills`, `CODEX_HOME/skills`, the plugin cache, a remote-skills
  folder, and a hidden folder of preinstalled system skills. For hosted
  containers the desktop app lists `SKILL.md` files with a ripgrep scan that
  skips `.git` and `node_modules` and stops at 512 results. A skill's scope is
  "repo" if it lives under the working directory and "user" otherwise.
- The binary embeds several system skills, including `imagegen`,
  `openai-docs`, `skill-creator`, `skill-installer`, and a review skill that
  can only be invoked explicitly. A couple of curated onboarding-style skills
  ship on disk.
- Skills config supports toggling bundled skills, including or omitting the
  instructions block, a token budget for the catalog, and per-skill
  enable/disable by path or name. Several rollout gates control skill search,
  dependency install prompts, plugin hooks, remote plugins and sharing.
- Prompt injection is catalog-only. A developer-message section lists each
  available skill's name, description and location. Paths can be shortened
  with root aliases defined once in a small alias table to save tokens. The
  model reads the body later using its ordinary file tools.
- Plugin skills are namespaced `plugin:skill`. Template skills must match a
  strict lowercase slug pattern with a fixed prefix.
- Skills come from three sources: the filesystem, the execution environment,
  and an orchestrator. Orchestrator skills are listed and read through a
  `skill://` identifier scheme.

### Connectors, deferred tools and install prompts

- The prompt describes a connector as a group of MCP tools inside one virtual
  connectors server. Users mention connectors with an `app://` link. Their
  tools are normally deferred and found through `tool_search`, and the model is
  told not to browse MCP resources to find them.
- `tool_search` runs BM25 over the metadata of deferred tools. Matching tools
  become callable on the next model request.
- An `[apps]` config table sets default enablement, default approval mode,
  whether destructive and open-world tools are allowed, which reviewer handles
  approvals, pool placement, and links. Destructive and open-world tools likely
  map to the standard MCP tool annotation hints.
- Approval UI choices are: allow once, allow for the rest of the session, or
  allow permanently. Only the root thread can surface an approval request;
  subagents cannot.
- A server-provided list of available but uninstalled plugins can be added to
  the prompt, along with a tool that lists installable plugins and a tool that
  asks the user to install one. The install tool is root-thread only. Its
  description limits it to cases where tool search has already failed and
  forbids adjacent or speculative recommendations. A user can persistently turn
  off a suggestion.

### Hooks

- Events: `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`,
  `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`,
  `SubagentStart`, `SubagentStop`, `Stop`, `Interrupt`.
- Hook definitions are a matcher plus a list of handlers. Each definition also
  has stored enabled/trust state, with trust recorded as a hash of the
  definition.
- There are four handler types: command, prompt, agent, and MCP tool call.
- Input fields include session id, transcript path, cwd, event name,
  permission mode, the subagent transcript path, a stop-loop guard, the last
  assistant message, tool input, turn id and model. Each event has its own JSON
  schema.
- Output can return a decision with a reason, a permission decision, rewritten
  tool input (pre-tool), extra context (only on some events), output
  suppression, and a status message. Decisions that don't fit the event are
  rejected with validation errors.
- Bundled browser and desktop-control plugins use MCP-tool hooks on `Stop`,
  `Interrupt` and `SubagentStop`. Each hook calls an end-of-turn tool on the
  plugin's own server, with the event name, session or agent id, and turn id
  filled in from templates. This appears to let stateful REPL servers release
  locks and flush state at turn boundaries without the model doing anything.

### Elicitation

- The standard MCP `elicitation/create` is supported. The client also
  advertises a vendor form extension (plus an older alias) in its initialize
  capabilities.
- The vendor form request adds an auto-resolution timeout (30 s in the bundled
  picker). It also lets JSON-schema properties carry a vendor input annotation
  that renders file or gallery pickers with preview options and accepted
  upload types. An older image-picker type is still recognized. Results are
  accept, decline, or cancel.
- The desktop app sends elicitations to a request panel that renders single
  choices, standard forms and upload pickers.

### MCP Apps (UI resources)

- Tools reference an HTML resource with MIME type `text/html;profile=mcp-app`
  through `_meta.ui.resourceUri` (a `ui://` URI). The older Apps SDK output
  template key is also accepted.
- A visibility setting of "app" marks a tool as callable only from its widget,
  so the model never sees it. A vendor `_meta` key declares launch points in
  the app (global, per-thread, settings). Icon style can also be set.
- The widget and host talk over a `ui/*` postMessage bridge. Requests cover
  initialize, sending a message, updating model context, opening links,
  changing display mode, downloading files and teardown. Notifications cover
  streamed and partial tool input, tool results, cancellation, size changes,
  host-context changes, and sandbox readiness.
- Each widget gets its own origin. Its CSP is built from domain lists declared
  on the resource (connect, resource, frame, base-URI). Microphone, geolocation
  and clipboard-write are granted only on request. First-party and third-party
  widgets are treated differently, and a follow-up message started by a widget
  may need the user's confirmation.
- Content attached by apps is sorted into "user" and "untrusted" buckets before
  it reaches the model. Anything that isn't an explicit user message counts as
  untrusted.

### Built-in template-picker server

- This is a zero-dependency Node MCP server using newline-delimited JSON-RPC.
  The host injects it as an MCP server and passes it, through an env var, a
  JSON catalog of enabled template skills. The catalog is size-budgeted and
  each description is cut to 600 characters.
- It has two tools. The first lists templates so the model can rank them. The
  second offers at most 10 of them in an elicitation gallery with image
  previews (caps: 8 / 32 MiB and 16 / 32 megapixels). It returns either the
  chosen skill's name and path, or the path of an uploaded reference file. In
  the upload case, a second form asks whether to save it for reuse.
- Supported kinds: document, presentation and spreadsheet (docx, pptx, xlsx),
  plus their Google Workspace equivalents.
- Reads are hardened: resolve the real path, confirm it is inside the expected
  root, and cap `SKILL.md` and `plugin.json` at 64 KiB. Scanning is limited to
  32 marketplaces and 100 templates.

### Prompt guidance (paraphrased)

- **Plugins:** a plugin is a local bundle of capabilities and is never invoked
  as such. The model should use the skills, MCP tools and connector tools it
  contributes. When the user names a plugin, the model should favor that
  plugin's capabilities for that turn.
- **Skills (older template):** use a skill when the user names it or the task
  clearly matches its description. Don't carry a skill over to later turns
  unless it is mentioned again. Never hand reading or interpreting a skill off
  to a subagent.
- **Skills (template for newer models):** don't pick a skill just because of
  keyword overlap or because one happens to be available.
- **Skill transparency:** if a skill makes the model pause, ask for
  confirmation, or stop short, it must point to the specific `SKILL.md` and
  cite the instruction that caused it.
- **Browser plugin skill:** before acting on a linked resource, first check
  available and deferred tools for a connector, API or CLI rather than driving
  the UI.
- **Recording skill:** once event capture has started, end the turn rather
  than polling or waiting for the user.
- **Skill authoring:** assume the agent is already competent and include only
  what changes its decisions.

## Harness design takeaways

- **T1** — *One tool substrate beneath several packaging layers.* Let MCP be the sole
  extension point for tools. Skills, plugins, connectors and even the desktop
  host's own tools (proxied over a local socket) then all show up as
  `mcp__server__tool`. Approval, timeouts, tracing and tool search need no
  special cases.
- **T2** — *Dotted per-plugin config overlay.* Key overrides by plugin,
  marketplace and server, so the host can turn bundled servers on or off per
  environment (local, WSL, remote) without editing plugin files. Ship servers
  disabled and let the host decide.
- **T3** — *Put per-server and per-tool policy in config, not code.* Default
  and per-tool approval modes (auto / prompt / writes / approve), output token
  limits, allow and deny lists, timeouts, readiness mode and a parallel-call
  flag form a matrix worth copying. Thread-mutating tools should prompt even
  inside a trusted first-party server.
- **T4** — *Give tools an explicit context-placement axis.* Decide per server
  whether its tools stay resident, sit behind BM25 tool search, or
  exposed in the code-mode scripting namespace. Core REPL tools should never
  be deferred.
- **T5** — *Inject only a skill catalog.* List name, description and location,
  with root aliases to save tokens, and let the model read `SKILL.md` lazily
  with its normal file tools. Add an explicit-only invocation policy for heavy
  or risky skills, and a token budget for the catalog.
- **T6** — *Skills declare their dependencies.* When a selected skill needs an
  MCP server or env var that is missing, the harness can offer to install the
  server or ask for the variable. A silent failure becomes a guided setup.
- **T7** — *Hooks that call MCP tools.* With this handler type, a plugin's
  stateful server gets lifecycle callbacks (stop, interrupt, subagent stop)
  with templated session and turn ids. Compared with shelling out to scripts this is
  tidier, and it binds cleanup to turn boundaries. Also support pre-tool input
  rewriting, per-event extra context, and hash-based trust for hook
  definitions.
- **T8** — *Read rival ecosystems' manifests.* Parsing Claude and Cursor
  plugin and marketplace manifests, and importing `CLAUDE.md`, `.cursorrules`
  and hook files, costs little and gives you an existing ecosystem from day
  one.
- **T9** — *Treat elicitation as a first-class UI channel.* Support standard
  elicitation plus a vendor extension negotiated at initialize time, with rich
  schema-driven widgets (file and gallery pickers, auto-resolution timeouts).
  Servers should degrade cleanly when the client lacks the extension, and translate
  accept / decline / cancel into a result the model can reason about.
- **T10** — *MCP Apps for rich UI.* Each tool references a `ui://` HTML
  resource; the widget converses with the host through a `ui/*` bridge (streamed tool input, results,
  model-context updates, messages, display mode). Hide widget-only tools from
  the model, declare launch points in metadata, and sandbox each widget on its
  own origin with a CSP built from the resource's declared domains.
- **T11** — *Treat app-produced content as untrusted by default.* Sort
  attachments into user and untrusted buckets before the model sees them. Only
  explicit user messages are trusted, and follow-ups started by a widget can
  require confirmation.
- **T12** — *Gate install suggestions behind a tool rather than prose.* Use a
  server-supplied list of installable plugins and an install-request tool that
  is root-thread only, not run in parallel, used only after tool search fails,
  and confirmed by the user. This stops hallucinated installs and
  recommendation spam. Remember "don't suggest again".
- **T13** — *Routing rules live in descriptions.* Plugin and skill
  descriptions encode aliases, a preference for connectors, APIs and CLIs over
  UI automation, and explicit negative rules. Before loading, the description
  is the model's only signal, so evaluate it like any other prompt.
- **T14** — *Harden every metadata read.* Resolve real paths, check they stay
  inside the root, cap sizes (manifests at 64 KiB, skill metadata at 1 MiB), enforce
  strict name patterns, require a single version directory, and require the
  manifest name to match the folder. Cached plugins are still untrusted input.
- **T15** — *Pin the MCP runtime through a launcher shim.* Resolve Node through
  a fallback chain (env override, bundled runtime, cached runtime, `PATH`) so
  plugins work without a user-installed Node. An env allow-list per server
  keeps secrets from reaching every server by default.

## Open questions

1. What exact format does a plugin's `.app.json` use for connectors, and how do
   its app ids map into the connectors server's tool names? No bundled plugin
   ships one.
2. How do the "wait for tool catalog" readiness mode and the optional-server
   grace period interact? Does a turn start before optional servers finish
   listing their tools?
3. How do the prompt and agent hook handlers work: which model runs them, what
   output do they return, and can they block?
4. What does the rendered skills catalog look like (line format, alias-table
   syntax), and what is the default token budget?
5. Does the code-mode host expose MCP tools as scripting namespaces, and how
   are namespaces excluded or forced to direct calls? This overlaps with other
   briefs.
6. How are plugin-sharing principals (workspace or user) enforced server-side?
7. Which fields does tool search index with BM25 (tool name, description,
   plugin, connector)?
