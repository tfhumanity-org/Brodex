# 00 — Desktop architecture  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

The macOS desktop app is the Codex desktop client shipped under the ChatGPT
brand (with Codex as an alternate name). It is built on an Electron-compatible
API, but the underlying engine is an OpenAI-maintained Chromium fork (Chromium
154) rather than stock Electron. The UI is one React single-page app loaded
from a custom `app://` scheme, and a sidebar switch selects the product mode
(ChatGPT, Codex, and a third work-oriented mode that appears not yet
available). None of the agent loop runs in the UI. The loop lives in its own
Rust binary, `codex app-server` (CLI release 0.160), reached either through
newline-delimited JSON-RPC-style messages on stdin/stdout or through a
WebSocket connection to a long-lived local daemon. The
Electron main process brokers between the two and can serve several hosts and
several windows at once. Renderer windows never talk to the agent process
directly.

## Diagram

```
+------------------- Desktop app (Chromium fork + Electron-style API) -------------------+
|                                                                                         |
|  Updater bridge (Sparkle)      MAIN PROCESS                                             |
|  Native addons --------------- early bootstrap -> network check -> bootstrap -> main    |
|                                  |-- window manager                                     |
|                                  |-- host connection registry (hostId -> connection)    |
|                                  |-- cross-client IPC router (per-user unix socket)     |
|                                  `-- worker thread (git queries, telemetry export)      |
|                                        ^  invoke (validated)     | chunked token stream |
|                                        |                         v  (acked)             |
|  RENDERERS (sandboxed, context-isolated)                                                |
|    primary / detached / overlay / hotkey windows -> preload bridge (send + "message")   |
|    React SPA with product-mode switch                                                   |
|    MCP-app sandbox frames (origin allowlist, MessagePorts)                              |
|    in-app browser pages (WebMCP tools, annotation mode)                                 |
+----------------+----------------------------+-------------------------------+-----------+
                 | spawn, stdio JSONL          | WebSocket over unix socket    | WSL / SSH /
                 v                             v (persistent local daemon)     v paired remote
        bundled `codex app-server`       `codex app-server` daemon       remote app-server
          |- code-mode host (V8)
          |- bundled rg on PATH, bundled zsh, voice host
          `- MCP servers from bundled plugins (app tools pipe, Node REPL, ...)
```

## Mechanics

### Bundle and runtime

- The app executable is a small stub; the engine lives in a large
  framework bundle that also carries Chrome-style helper apps (renderer, GPU,
  service, alerts, crash handler, app-mode loader). A second parallel set of
  helpers exists whose purpose is unclear (see Open questions).
- The Chromium fork likely is the same one used by OpenAI's browser product:
  the bundle carries a Chromium-style AppleScript suite (tabs, bookmarks),
  Chrome-like helper names, and the bundled plugin list references it.
- The fork exposes extra native bindings to JS, including enterprise update
  policies (relaunch windows and notifications).
- Early startup turns on Chromium switches for WebMCP support, forces some
  WebRTC field trials, and sets a scoped user-agent prefix for OpenAI and
  ChatGPT domains. A developer-only environment hook can inject more switches.
- On first run it migrates the older native ChatGPT app: if that app occupies
  the expected path, its background updater is stopped and it is renamed to a
  "Classic" name.
- The app is scriptable via AppleScript, hosts a custom Dock tile plugin, handles
  `codex://` URLs, and registers as an optional http/https handler, and a viewer for
  spreadsheets, documents, slides and folders. It also declares a document
  type for skills.

### Startup sequence

1. An early bootstrap stage applies Chromium switches, registers the
   privileged `app` scheme plus a second service-worker-capable scheme, and
   queues any "open path" events that arrive before the UI is ready.
2. It awaits a network-permission initialization step. If that fails, the
   app logs the failure and exits with status 1 before any further code
   loads.
3. The main bootstrap resolves a build flavor (environment variable, then a
   package field, then a default). Flavors span dev, nightly, internal alpha,
   public beta and production, among others. The flavor gates devtools,
   auto-update, browser-use peer authorization, and icons.
4. If connecting to the app-server is slow, a small progress window appears
   telling the user that startup is still in progress and may take minutes.
   The same window is reused for a one-time database backfill.

### Process model

| Process | Role |
|---|---|
| Main (browser) process | Manages windows, brokers app-server traffic, runs the updater and the IPC router, loads native addons. |
| Main-side worker thread | Git queries (current branch, branch diff stats, worktree list, review and status summaries), telemetry export. Talks to main over its own message protocol and can issue calls back to main. |
| Renderers | One SPA in several window kinds: primary, detached, HUD, hotkey windows, overlays, popups, preview and comment windows. Only some kinds may embed `<webview>`. |
| MCP-app sandbox frames | Third-party app UI in an isolated frame. The origin must match a dedicated sandbox domain allowlist; communication goes over MessagePorts handed in once. |
| In-app browser pages | A dedicated preload forwards WebMCP tool changes to main and supports annotation/comment mode and page events. |
| `codex app-server` | Separate signed bundle with its own bundle id and JIT entitlements, launched through a shell shim. |
| Native helper executables | Global bare-modifier hotkeys, LaunchServices / Dock tile repair, audio spectrum for the voice UI. |
| Computer-use service | A separate helper app under the Codex home directory, spawned and driven over XPC by a native addon. |

The main SPA window runs with context isolation on and Node integration off;
devtools follow the build flavor. Auxiliary windows are fully sandboxed with
devtools disabled, and at least one runs with JavaScript disabled.

### Renderer to main bridge

- The preload exposes a deliberately small API: one "send message from view"
  call (an IPC invoke), and inbound messages are re-dispatched as ordinary
  window `message` events. This lets the same SPA run unchanged inside a
  VS Code webview, which uses the same postMessage shape.
- Per-worker channels let the view talk to main-side workers.
- A handful of synchronous getters supply startup data (error-reporting
  options, build flavor, a shared-state snapshot, system theme, initial
  sidebar state) so first paint needs no round trip.
- Other helpers: file and link drag start, context menus, resolving a dropped
  file's path, device-attestation support check, and handing a MessagePort to
  main to connect an app host.
- A "remote hosted picture-in-picture" path decodes encoded video frames in
  the renderer with WebCodecs and posts them to the page, likely to show a
  remote or computer-use session.
- A shared-object mirror keeps a key-value copy of main-owned state inside
  the preload, so the view can read it synchronously; updates flow both ways.
- Large main-to-view payloads are streamed as a chunked JSON token stream
  (start / chunk / end envelopes carrying tokens such as array-start, key,
  string fragment, value), batched to a target byte size. The renderer acks
  each chunk, and an unacked-envelope buffer applies backpressure.
- Every main-side handler first checks that the IPC sender is a trusted
  frame, and browser-sidebar payloads are schema-validated.
- A few message types are handled in main directly (open in main or new
  window, open browser, show settings, quit, reload bundled plugins, updater
  and feature-gate changes, menu-bar and tray/dock menus, overlay events).
  Other messages are delegated to a context object owned by the sending window.

### Main to `codex app-server`

- **Launch.** The local server is started as `codex app-server` with a
  feature flag that enables the code-mode host and with analytics on by
  default. Optional environment overrides for the ChatGPT and OpenAI base URLs
  are passed to the child as `-c` config overrides for the corresponding
  base-URL keys.
- **Binary lookup.** An explicit environment override comes first, then the
  CLI bundled in the app (described by a small package manifest giving
  version, entrypoint, resources dir and PATH dir), then a developer build.
- **Child environment.** `CODEX_HOME`, the CLI's own path (so the agent can
  call codex), a bundled Node path for MCP servers, a pipe path for app-level
  tools, an originator override naming the desktop client, JSON log format at
  warn level, a SQLite home, a preferred git executable, and PATH extended
  with the bundled `rg` directory.
- **Transports.**
  - *stdio*: one JSON object per line on stdin; stdout is split into
    lines by a reader that tracks buffered size; the last stderr line is kept for error
    messages. No reconnect.
  - *Local daemon over WebSocket*: used only when opted in via environment,
    without overrides, not on Windows, and when the daemon answers a version
    probe within 2.5 s. It connects over a unix socket under the Codex home
    and supports reconnect.
  - Host kinds: local, WSL (runs codex through a login shell in the chosen
    distribution), SSH (configurable remote command, with port forwarding for
    the browser), and a paired remote-control device.
- **Handshake.** An `initialize` request carries client info (a desktop
  client name and title, version) and capabilities: `experimentalApi`, an
  MCP-apps extension, form-style elicitation support, request attestation,
  and `optOutNotificationMethods` (the desktop opts out of diff-update
  notifications, among others). An `initialized` notification follows. The
  server can push config warnings that the UI shows with file, line and
  column.
- Desktop-side requests are `{id, method, params, trace}` with no
  `"jsonrpc": "2.0"` field; `trace` carries per-request trace context.
- **Protocol surface** (method families): `thread/*` (creating, resuming,
  forking, reading and listing threads; archiving; rolling back; injecting
  items; queued follow-ups; per-thread settings; realtime sessions),
  `turn/*` (begin a turn, steer it mid-flight, interrupt it), code review, `command/exec` and its
  write / resize / terminate variants, `process/*`, `config/*`,
  `mcpServer/*`, `account/*`, remote control, environments, fuzzy file
  search, Windows sandbox, and external agent config. Notifications include
  item start, agent-message deltas, reasoning deltas, turn diff updates,
  realtime events and login completion. Experimental methods are rejected
  server-side unless the client negotiated `experimentalApi`.
- **View-to-server bridge.** The view sends a request envelope naming a
  `hostId` plus scheduling options: priority, coalescing, timeout, absolute
  expiry, whether to retain the response, and thread access. Main routes it to
  that host's connection. Responses, server notifications, and
  server-to-client requests (approvals, elicitation) come back as typed
  envelopes; there are also abandon, delivery-status, initialized and
  connection-changed messages. The envelope names say "mcp" for historical
  reasons, but the payloads are app-server JSON-RPC.
- Windows that are background-throttled only receive messages for threads
  they own; responses and initialization messages always get through.
- Each request records queue time, wait for host readiness, peak pending
  concurrency, reconnect attempt, request and response size, and transport.
- **Supervision.** Connection state carries typed errors (the user must sign
  in, the server needs restarting, no codex binary exists on the remote host,
  or the handshake timed out). Reconnect
  waits 1000 ms. A transport that cannot reconnect (stdio) broadcasts a fatal
  error to every window.
- The CLI ships its own daemon management (a `codex app-server daemon`
  subcommand that can launch, halt, report the version of, or upgrade the
  daemon), a startup lock, listen modes for stdio, a
  unix socket, a WebSocket address or off, and a separate exec server that
  exposes a remote environment over WebSocket.
- The LaTeX preview is a second, independent app-server client; it uses
  `command/exec` to run the bundled `tectonic` inside the codex sandbox.

### Cross-client IPC router

- One router per user lives on a unix socket in a private directory (mode
  0700, ownership checked), with a temp-dir fallback and a named pipe on
  Windows. If a router is already listening, the process joins as a client.
- It supports request/response with client discovery (each client is asked
  whether it can handle the request; the first yes wins; 10 s timeout),
  broadcasts with optional target lists, and an `initialize` that assigns a
  client id.
- Broadcast topics cover thread stream state and following, read state,
  archive/unarchive, queued follow-ups, cache invalidation, thread-owner
  discovery, IDE context and ping. Traffic is grouped as snapshots, patches
  and follower coordination.
- This appears to let the desktop app and IDE extensions see the same live
  thread: whichever client owns a running stream drives it and the others follow it through snapshot
  plus patches, while IDE context supplies open-editor state.

### ChatGPT and Codex modes in one app

- The sidebar mode switch is heavily used throughout the renderer. The Codex
  mode is pitched at building and shipping software; the work mode at
  creating and learning.
- Permission copy has ChatGPT-mode variants, for example a full-access
  confirmation saying ChatGPT can run commands, reach the internet and edit
  files anywhere on the machine. In other words the local agent is reachable from ChatGPT mode too.
- Both modes likely sit on the same app-server and thread model; switching
  alters the default surface, prompts and permission wording, and possibly the
  collaboration mode or tool set. The brand itself can be switched by an
  update.
- Icons are chosen per flavor and brand, with a user Dock-icon preference.
- Some app settings are marked as readable and writable by the agent (for
  example Dock icon and reduced-motion preferences).

### Updates

- macOS uses Sparkle through a native bridge, with an EdDSA-signed appcast. A
  backend-served appcast can be switched on remotely; that choice is
  persisted locally along with a random installation id, and query
  parameters carry beta status and plan type.
- Internal builds use an authenticated CDN feed.
- The bridge forwards check results, lifecycle state, download progress and
  ready-to-install events to JS, supports background downloads and custom
  headers/cookies, and suppresses Sparkle's own UI so the app owns the update
  experience.
- Forced updates exist (a timer that schedules install), alongside engine
  relaunch policies (notify, relaunch window, fast relaunch when outdated).
- Windows uses an MSIX/Store updater; Linux uses a package updater.
- Each app build carries one exact CLI version, and release builds refuse a
  pre-release minimum app-server version. The daemon has its own update
  command.

### Native addons and helpers

| Component | Role |
|---|---|
| Computer-use bridge (Swift) | Launches the computer-use service and exchanges messages with it; also drives the on-screen cursor overlay, pausing/resuming action history, modifier capture and window enumeration. |
| Updater bridge | Sparkle integration (above). |
| Device attestation | Apple DeviceCheck; likely backs the `requestAttestation` capability. |
| Browser-use peer authorization | Checks the code signature of any process that opens the browser-use pipe. |
| Remote-control device key | Hardware-backed P-256 key used to sign remote-control pairing payloads. |
| USB WebAuthn | Security-key registration and authentication for the in-app browser. |
| Input-monitoring check | macOS permission check for global hotkeys. |
| HID watcher | Detects a dedicated hardware keypad accessory. |
| AirPods mute | Mute gesture for voice and dictation. |
| Helper executables | Bare-modifier hotkeys, LaunchServices/Dock tile repair, audio spectrum. |
| Unpacked npm modules | `better-sqlite3` (local stores), `node-pty` (terminals), an Objective-C bridge, hardware SDK. |

### Bundled binaries and runtimes

- The CLI bundle (about 241 MB) plus a V8 code-mode host, a bundled `rg`
  prepended to the agent's PATH, a bundled `zsh`, and a voice host with
  GStreamer libraries listed in a sha256 manifest (used for realtime voice).
- Standalone `rg` and `tectonic` (LaTeX to PDF previews via `command/exec`).
- A dedicated Node 24 runtime with a Node REPL tool, Playwright, sharp and
  internal computer-use and browser packages. An environment contract tells
  the REPL which node, module directories, trusted code paths and trusted
  services to use, and distinguishes a plain Node REPL from a computer-use
  REPL.
- To serve the Chrome extension's native-messaging host, the bundled CLI is
  copied to a stable
  location under the Codex plugins directory, so the extension's native host
  keeps working across app updates; stale copies are stopped before being
  replaced.
- A cloud tunnelling feature (MASQUE over QUIC, driven by the CLI) restricts
  itself to an allowlist of origins shipped in the bundle.

### User-facing text themes

- A slow-start notice asking the user to keep the app open.
- A WSL error explaining that codex is missing in the distribution and how to
  install it or point to it.
- A generic error when the CLI or its runtime pieces cannot be found.
- Short taglines for each product mode.
- A CLI error telling the user to enable the code-mode host feature and
  install its helper.

## Harness design takeaways

- **T1** — Put a process boundary between UI and agent, and speak a versioned
  JSON-RPC protocol across it. The desktop app is only one client of `codex app-server`,
  just like the IDE plugins, the LaTeX preview helper and the browser
  extension's native host.
  Build the loop as a headless server first; every UI sits on top.
- **T2** — Hide transports behind one connect step. stdio JSONL is simple but
  dies with its parent; a WebSocket to a persistent daemon over a unix socket
  survives UI restarts and can reconnect; WSL, SSH and paired remotes slot in
  the same way. Transport is chosen per host, and every request names its host id.
- **T3** — Negotiate capabilities in `initialize`: experimental-API gating,
  extensions such as MCP apps, per-client notification opt-outs for noisy
  streams, and flags for attestation and elicitation. Enforce the gates on
  the server, not just the client.
- **T4** — Give the UI broker real scheduling: priority, coalescing, timeouts,
  absolute expiry, retained responses, abandonment, delivery-status events and
  per-request metrics (queue wait, host concurrency, bytes). Carry trace
  context inside each message.
- **T5** — Stream large payloads as acked JSON token chunks. It sidesteps
  enormous single IPC messages so the interface stays fluid; the same approach suits
  any harness-to-UI pipe that carries lengthy conversation histories.
- **T6** — Route by ownership. Throttled windows get only events for threads
  they own, and a cross-process router locates capable clients and
  thread owners, letting many frontends mirror a single live thread from a
  snapshot plus patches rather than duplicating the work.
- **T7** — Let the agent call back into its host. Exposing the CLI path, an
  app-tools pipe and the bundled Node runtime via environment variables lets it
  spawn sub-agents, call app-level tools (thread creation, hand-off,
  automations) and use trusted REPL sessions, with approval behavior configured
  per tool in the plugin's `.mcp.json` file.
- **T8** — Ship a hermetic toolchain: pinned `rg`, `zsh`, Node, `tectonic` and
  a voice runtime, checksummed and placed first on PATH, so agent behavior
  does not depend on what the user has installed.
- **T9** — Pin the CLI to the UI version and guard minimum versions. Consumers
  installed separately (the Chrome native host) get a copied CLI at a stable
  path so app updates cannot break them.
- **T10** — Treat every IPC entry point as untrusted: verify the sender on
  each handler and schema-validate each payload. Frames hosting third-party UI should accept
  only allowlisted origins and receive their MessagePorts exactly once.
- **T11** — Keep the renderer bridge one-way and tiny: a single send call plus
  a `message` event lets one SPA run unchanged as a desktop window, an editor
  webview or a normal web page. Synchronous bootstrap getters and a shared-state mirror remove
  startup round trips.
- **T12** — Design explicit UX for slow startup and degraded states:
  delayed progress windows for server startup and database backfill, typed
  connection errors, and config warnings with file and line from the server.
- **T13** — Ship one product with mode switches instead of two apps. Both
  modes run on the same runtime and differ only in framing and permission copy;
  even the brand can change through an update. A harness can likewise offer
  personas over a single loop.
- **T14** — Keep heavy work off the UI broker. Repository queries (status,
  diff statistics, worktrees) and telemetry export happen on a worker thread
  that has a private message protocol (with cancellation) and can call back
  into main.

## Open questions

1. The view-to-server request path seems able to hit an in-process synthetic
   host that answers some methods without the app-server. What does it serve?
2. Is the second set of engine helper apps a separate profile or engine mode
   (for example in-app browser versus app UI), or a flavour tied to the
   browser product?
3. Does ChatGPT mode pass a different collaboration mode, model or base
   instructions in `thread/start`? Needs the renderer's thread-start
   parameters (covered elsewhere).
4. When is the local-daemon transport enabled in production builds: only via
   environment, or by a remote rollout gate? Only the environment check was
   seen.
5. What does the startup network-permission step actually enforce (proxy,
   certificates, managed network policy)? That step was not followed further.
6. Is the DeviceCheck token the source of attestation answers sent to the
   app-server, and which server-side calls depend on it?
7. Precise framing on the wire (omitted fields, batching) and the schema of
   delivery-status updates were not fully enumerated.
