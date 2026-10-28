# 09 — Computer Use ("work with apps")  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

> The Computer History subsection was rechecked against desktop build 26.1007
> with CLI 0.162 and against the public help page (2026-10). The feature did
> not change between 26.930 and 26.1007 apart from a bundled plugin version
> bump; the recheck added detail and public-docs provenance.

## Overview

On macOS, Computer Use (CU) is its own Swift background app, signed and
updated independently of the main app. Every risky permission sits there:
Accessibility, Screen Recording, Apple Events and Contacts/Calendars access,
plus an optional login-window authorization plugin that can unlock the Mac
during an active turn. The model never talks to that app directly. It writes
JavaScript in a sandboxed REPL tool (`node_repl`). A CU object in that REPL
forwards each call over RPC to a trusted service module running in the REPL's
privileged worker. The trusted side sends length-prefixed JSON-RPC over a Unix
socket inside the service's app-group container. The service checks that the
connecting peer is code-signed by its own team, records the caller's parent and
OS-designated "responsible" process, applies app and URL policy,
performs the accessibility, screenshot and input work, and returns an
"app state": a screenshot file reference plus accessibility-tree text, often
as a diff. The same service also backs a few first-party connectors (Messages,
Calendar, activity recording).

## Diagram

```
 model ── tool call ──▶ codex ── MCP ──▶ REPL launcher
                                           │ spawns
                                           ▼
                                      node_repl
          ┌──────────────── untrusted cell (model code) ────────────────┐
          │ CU proxy object: call ─▶ rpc("<service>", {method, args})    │
          └──────────────────────────────┬──────────────────────────────┘
                                         │ trusted service request
          ┌──────────────── trusted worker (allowlisted modules) ───────┐
          │ policy wrapper ─▶ approval as MCP elicitation               │
          │ holds the native pipe to the CU service                      │
          └──────────────────────────────┬──────────────────────────────┘
               4-byte LE length + JSON-RPC 2.0 (ping / request)
               Unix socket in the service's app-group container
                                         ▼
   ┌────────────────── CU service (background Swift app) ─────────────────┐
   │ peer signing-team check · sender context (parent + responsible proc)  │
   │ org policy via app-server · per-app approvals · URL blocklist         │
   │ app sessions → AX tree + screenshot capture (with diffing)            │
   │ per-window input · focus enforcer · input monitor · cursor · PiP      │
   │ lock-screen coordinator · Messages · Calendar · activity recording    │
   └──────┬──────────────────────────────┬────────────────────────────────┘
          │ XPC                          │ local socket
          ▼                              ▼
   lock-screen guard helper      login authorization plugin
   (watches physical input)      (screensaver unlock right)

 Desktop main process: host-services socket; when the CU socket is absent the
 REPL asks the desktop to start or adopt the CU service via a native bridge.
```

## Mechanics

### Packaging and trust anchors

- The service is a background-only app (no Dock icon) requiring macOS 14.4 or
  later. It embeds a CLI client, a lock-screen guard helper, and an installer
  for the authorization plugin.
- Its entitlements cover an app group, Apple Events automation, Contacts and
  Calendars, and shared keychain groups. Protobuf in the module graph appears
  to be used only for analytics, not for the request wire format.
- The helpers ship macOS launch constraints, so each one only runs if its
  parent process is signed by the vendor's team.
- The runtime copy lives under `$CODEX_HOME/computer-use/`, and the REPL
  package prefers that location.
- Keeping CU in its own small app likely ties the TCC grants (Accessibility,
  Screen Recording) to a stable binary rather than the large, fast-changing
  host app. It also lets CU ship on its own update feed.

### Wire protocol

| Aspect | Value |
|---|---|
| Transport | Unix domain socket in the app-group container (path overridable by env for development) |
| Framing | 4-byte little-endian length, then UTF-8 JSON; max frame 8 MiB |
| Protocol | JSON-RPC 2.0, methods `ping` and `request` |
| Handshake | `ping` carries the client API version; the server must answer with an exact expected API version string (revision 5), otherwise the client stops with a version-mismatch error telling the user to relaunch the app |
| Envelope | client API version, turn metadata, absolute deadline, request type name, request body |
| Ordering | requests serialized per connection |
| Default timeout | 120 s |

- Request types cover listing apps, starting an app session, getting app state,
  performing an action, querying app policy, and starting/stopping audio
  recording. The service resolves type names dynamically. Marker traits flag
  requests that need system permissions or that must not trigger lock-screen
  auto-unlock.
- All actions go through one tagged union: click (by element id or
  coordinate, click count, mouse button), drag, paste (text + format),
  secondary action on an element, key press, scroll (direction, pages), set
  value, select text (by prefix/suffix), and type text.
- There is a second transport. An Apple Event delivers an XPC bootstrap port,
  the service checks the sender's PID, and requests then travel over XPC. That
  path can also pass file descriptors for attachments. The native bridge used
  by the desktop has its own separate version string.
- Errors use a stable numeric table from -10000 to -10020. It covers an
  unauthenticated sender, app not allowed, permissions not granted or still
  pending, no active session, user stopped the session, incompatible client
  version, blocked URL, user intervened, ambiguous app, and screen locked.

### Peer authentication

Authentication of socket callers happens in two separate layers (observed).

- **Direct-peer check.** For each request the service takes the connecting
  peer's audit token, asks the system code-signing API for the code object
  behind that token, and reads the team identifier from its signing
  information. That value must equal the team identifier the service reads
  from its own signature. Nothing here depends on PIDs or executable paths, so
  PID reuse or a copied binary at a trusted path does not help an attacker. A
  variant that matches an explicitly named team also exists. Results are
  cached behind a lock.
- **Sender context.** A distinct resolver builds a context object for each
  caller with two, and only two, linked identities: its parent and the
  process macOS regards as "responsible" for the caller. Each is recorded by
  executable, signing identifier and team. There is no unbounded ancestry
  walk; earlier readings of a deep chain were wrong.
- The three authorization failure reasons (missing parent, untrusted parent,
  relay without a trusted ancestor) map onto that context. A relay such as
  the REPL or node appears to be accepted when its parent or its responsible
  process resolves to a trusted vendor-signed identity (inferred; the exact
  boolean combination with the direct-peer check was not recovered).
- The same context also carries whether the caller may trigger lock-screen
  auto-unlock.
- The desktop's browser-automation pipe mirrors this design in a native Node
  addon: it reads the peer token from the socket, resolves the code object
  through the same system code-signing API, checks the parent and grandparent signing identifiers against an
  allowlist of the app's release channels plus the REPL, and reports a reason
  (untrusted ancestry, missing signing identity, untrusted signing identity).
- The login authorization plugin verifies its socket peer's identifier and team
  in the same way.

### REPL trusted/untrusted split

- The launcher registers the trusted service modules (Computer Use and
  browser) and passes only a very short env allowlist into untrusted cells. It
  also sets a startup banner import and overrides the tool description with
  text assembled from Markdown files.
- Trusted modules must resolve inside configured trusted code paths and must
  export an RPC handler. Untrusted cells only get a narrow capability set: rpc,
  write output, emit image, read/set response metadata, create an elicitation,
  suspend the cell timeout, a native pipe handle, and an app launcher.
- The CU object in the cell is a proxy. It first asks the service for the target
  and method list, then forwards every call as an execute RPC. If the RPC
  channel is missing it throws. So code the model writes has no way to reach the CU socket directly; the
  socket belongs to the trusted worker alone.
- Turn metadata (call and item ids) travels from the MCP request into every CU
  request. This lets the service link each action to its thread and turn,
  for example when deciding about auto-unlock.

### Policy and approval pipeline

1. **App policy lookup** returns a decision (allowed / denied / forbidden), the
   resolved target (bundle id, display name, path, risk level, warning text),
   and whether persistent approval is allowed. "Denied" is reported as an
   organization block. "Forbidden" is reported as a safety block.
2. **Built-in forbidden set**: screen-continuity, Keychain Access, Passwords,
   the major third-party password managers, all common terminal emulators,
   every ChatGPT/Codex/Atlas build, and Safari (including its preview
   channel). Terminals are off-limits since command-line work is meant to go
   through the sandboxed exec tool. The host apps are excluded
   to stop the agent driving itself or spoofing approvals. The check is a
   single pure predicate over the target's app identifier, a chain of
   matchers kept in code and separate from the org-policy and approval
   layers (observed).
3. **Organization policy** comes from the app-server's managed requirements:
   whether browser/computer use is allowed at all, whether persistent approval
   is allowed, and a configurable default level of app access. The result is cached.
4. **Human approval** is an MCP form elicitation asking whether CU may use the
   named app. It carries the risk level, a subtitle, the tool name and display
   parameters, and the allowed persistence scopes (session only, or session and
   always). An answer replayed from persisted state skips both the prompt and
   telemetry.
5. Every tool goes through the policy wrapper. The REPL cell timeout is paused
   while the human decides.
6. The risk subtitle warns that letting the assistant use the app adds risk,
   especially prompt injection leading to data theft or loss, and asks the
   user to watch closely.
7. After approval the arguments are copied into a frozen, plain-data object
   with the app replaced by its resolved path. This blocks getter/Proxy tricks
   between check and use (TOCTOU).
8. A URL blocklist applies to browsers. It combines a cached list with a
   server-side site-status check scoped to the account and has a configurable
   failure behaviour. On a hit the session ends and the model is told the URL
   is off-limits even if the user navigates there themselves.
9. System audio recording is a separate high-risk approval, limited to
   session-only persistence and off by default.

### Observation: app state = screenshot + accessibility text (+ diff)

- The app-state observation tool starts an app session if needed and returns the key window's
  screenshot and accessibility tree. The model must call it once per turn
  before acting on that app. Other actions fail with an instruction to call it
  first.
- Accessibility-tree diffing is on by default. Responses are a diff against the
  previous tree, a cumulative diff against the first tree, or a "no change"
  notice. There is a line budget and an opt-out flag for a full tree. Diff
  lines use `~ + -` markers.
- Elements are addressed by index. Stale elements are re-fetched by matching
  criteria, and multiple matches produce an "ambiguous, re-observe" error.
- Each action is followed by a settle delay of roughly 1 s, extended by as
  much as 5 s when loading indicators are visible. Action results are just a short acknowledgement
  telling the model to fetch state again.
- Natively, every actuator (coordinate click/drag, element click, set value,
  keyboard action, paste, select text, secondary action) takes a flag that
  makes it return a fresh observation in the same call (observed). The
  REPL-facing results described above are still bare acknowledgements, so the
  inline path likely serves other clients or is selected per call (inferred).
- No dedicated per-action rate limiter exists in the service (observed). Pacing
  comes from the post-action settle delay and from human approval, not from a
  token bucket.
- Per-app operating notes (for example Slack, Notion, Clock, Apple Music,
  Spotify, iPhone Mirroring) ship as Markdown. They are prepended once per app
  per client session inside a dedicated tag on the first observation.
- Capture uses the system's modern stream-based screen-capture framework
  rather than the legacy window-list image call, and encodes images with an
  a set lossy-compression quality, which makes image weight adjustable
  (observed).
  The accessibility tree is read through the standard accessibility client
  API (attribute reads, hit-testing, action lists, perform/set). The refresh
  routine has switches to skip the screenshot and to disable tree diffing,
  giving a cheap accessibility-only refresh path (observed).
- Screenshots come back as `file://` references. The model must explicitly emit
  the image to see pixels, so accessibility text is the cheap default and
  vision is opt-in.
- Pasting uses the system pasteboard, after which the user's original
  clipboard contents are put back. The model gets a warning if the user's own clipboard activity
  may have interfered.

### Sharing the machine with the user

- Synthetic input is addressed to one process and window, not posted to the
  global event stream (observed). Mouse events are built as system input
  events, stamped with the target window's id in the fields that name the
  window under the pointer and the window able to handle the event, placed in
  window coordinates via a private window-server interface, and delivered with
  the per-process post call. Keyboard events are built and delivered the same
  way. The service never uses the global post call.
- Alongside the events the service sends synthetic app-activation and
  key-focus notifications to the target window, so a background app behaves
  as if it were key, and whatever the user has in front stays untouched.
- Focus enforcement holds focus on the target app for synthetic events while
  the work runs "in the background", and blocks other processes from stealing
  focus. Because events are process-addressed, key presses and typing reach
  only the target app and cannot fire global shortcuts.
- A debounced user-interaction monitor treats real input as an intervention and
  ends the action with a "user intervened" error. A power assertion keeps the
  display awake while CU is working.
- A visible agent cursor overlay shows where the agent acts. A
  picture-in-picture panel, fed by video the service encodes from the agent's
  window, shows progress per thread with stop and hide controls.
- Stopping is scoped to the turn. If the user stops a session, the model is told
  to stop, send a final message acknowledging it, and offer to continue; CU
  works again on the next turn. The Stop, SubagentStop and Interrupt hooks of a bundled plugin call a
  hidden end-of-turn MCP tool so session state is torn
  down deterministically.

### Permissions onboarding

- A guided window walks the user into System Settings with animated
  transitions and tracks four states: none granted, Accessibility only, Screen
  Recording only, both granted.
- While permissions are pending, the model is told to call the tool again and
  not to end its turn.
- The service models five permission kinds: Accessibility, Screen Recording,
  Calendar, Contacts and Full Disk Access. Each request declares the set it
  needs, and a requester prompts only for missing ones (observed).
- On the desktop side, an Input Monitoring check gates a hotkey (press both
  Command keys) that attaches a snapshot of the current app, and global
  dictation.

### Lock-screen auto-unlock

- A coordinator tracks whether auto-unlock is enabled, which threads are
  active, attempts per locked episode (capped), and suppression state. Other
  parts watch the system lock/unlock notifications, operate the lock-screen
  password field through accessibility, show an overlay saying the assistant is
  using the Mac (press any key to take over), and monitor physical input.
- A user-run installer, which needs administrator rights, changes the
  screensaver-unlock right in the authorization database to add a
  SecurityAgent plugin. It also has an uninstall path that restores stock
  behaviour.
- The plugin connects to a local socket owned by the service, checks the peer's
  audit token, signing identifier and team, and reads an allow/deny answer.
- Likely flow: the screen locks during an active CU turn. The service submits
  the lock-screen field without knowing any password. SecurityAgent calls the
  plugin, the plugin asks the service, and the service allows the unlock only
  if an active thread holds an unlock lease. The guard helper draws the overlay
  and watches for physical input. Any physical input pauses auto-unlock and
  hands control back to the human.
- The lock-screen controller receives four timing values (overall timeout,
  poll interval, submit delay, verification delay) as constructor parameters,
  and the coordinator receives its per-episode attempt cap the same way, so
  none of these are hard-coded literals (observed; defaults not recovered).
  Unlock leases are retained and released per thread id through a shared
  broker that hands out single-use authorization attempts.
- Individual requests can opt out of auto-unlock. While the screen is locked,
  accessibility reads fail with an error asking for an unlock first.

### First-party connectors on the same service

The CLI client has subcommands for running as an MCP server (legacy path),
Calendar, Messages, activity history, activity recording, and status. Plugins
start it through a small launcher script under `$CODEX_HOME`.

| Connector | Tools / behaviour |
|---|---|
| Messages | `send_message`, `read_messages`, `find_chats`, `read_image`, `search_messages`, plus an activity-count tool; cursor pagination; per-response opaque local references that must not be reused across responses; approval persistence once / turn / session / always; single-use send plans with a tombstone so a retried send reports possible prior delivery instead of duplicating |
| Calendar | `create_event`, `get_event`, `list_events`, `delete_event`, `update_event`; 31-day query window; recurring edits target this event or future events |
| Activity recording | start / status / stop; after user confirmation captures clicks, keystrokes and window contents, capped at 30 minutes, into JSONL plus a session file; a skill turns the recording into a reusable skill |
| Activity history (Computer History) | opt-in event stream of interactions and accessibility context, no screenshots, kept as short-lived segment files (up to 48 hours); app and URL observe/do-not-observe policy with private browsing always excluded; a short-lived agent session summarizes it server-side into durable 10-minute and 6-hour Markdown memories; a bundled skill plus an MCP server with status, pause, resume, get-settings and update-settings tools (see next subsection) |

### Computer History (activity memory)

Computer History is an opt-in, rolling record of what the user does on the
Mac, turned into memories that later chats can draw on. The CU service does
the capturing; summarization and retrieval live in the agent harness.

```
 allowed apps / sites ── interaction + accessibility events ──▶ CU service
                                                                    │
            app-group container: segment folders (event log + metadata),
            a suppressed-events file; temporary, deleted within 48 h
                                                                    │
            periodic short-lived agent session (processed server-side)
                                                                    ▼
      memories dir / dedicated extension folder: 10-minute and 6-hour Markdown
                                                                    │
            later chats: memory injection (per-chat /memories control)
            + bundled skill and MCP tools (status, settings, search raw segments)
```

**Capture.**
- What is recorded is a stream of interaction events, not video. Evidence
  fields include the app, window, URL, selected and typed text, the focused
  element, the target of mouse or keyboard input, and accessibility tree
  content or diffs (observed).
- Clicks, typing, shortcuts, app switches and accessibility context are
  captured; there are no screenshots, no microphone or system audio, and the
  feature does not need the Screen Recording permission (public docs).

**Storage and retention.**
- Raw events are written per segment: each segment folder holds a JSONL event
  log that serves as the model-facing evidence and a metadata file with
  timing and event counts. A separate file tracks suppressed events (observed).
- These event files sit in the service's app-group container and are deleted
  after at most 48 hours. They are not encrypted, and other processes running
  as the same user can read the memory files (public docs).
- Durable output is Markdown in a dedicated extension folder under the
  memories directory: an instructions file plus resource files at two
  granularities, one per 10-minute window and one per 6-hour window, named
  with a UTC timestamp, an id, the granularity and a slug (observed). Memories
  persist until the user deletes them (public docs).

**Summarization.**
- A periodic, short-lived agent session produces the summaries. Event files
  are processed on the vendor's servers, are not kept after processing unless
  the law requires it, and are not used for training (public docs). No
  schedule is published; the two granularities are confirmed by file naming
  (observed).
- Memories used in later chats become context covered by the user's data
  controls, and the `/memories` control toggles per chat whether memories are
  used and contributed (public docs).
- The summarizer treats everything derived from observed content as untrusted
  and writes descriptive memory only (see Prompt surfaces and T15).

**Retrieval recipe (bundled skill, observed).**
- Check the feature's status first, then compare the current time with the
  newest segment's metadata to judge how fresh the history is.
- Coarse to fine: for broad questions read the 6-hour summaries, then drill
  into 10-minute ones; for recent or very specific questions search the raw
  segment logs directly with a text search tool.
- Event content is untrusted evidence, not instructions.
- Once history identifies the relevant app or document, move to that app's
  own connector or source rather than answering from observed text.
- Do not use the activity-recording connector for questions about background
  history.

**Settings model.**
- There is a default behaviour (observe or do not observe) for apps and a
  separate default for URLs, plus allow and block lists of rules. Each rule
  is scoped to an app (by bundle identifier) or to a URL domain, and domain
  rules also match subdomains (observed).
- A browser record with a URL must pass both the app policy and the URL
  policy; a record without a URL is judged by app policy alone. Within a
  scope, a block beats an allow. Private browsing is always excluded,
  whatever the rules say (observed).
- The agent changes settings only through the MCP tools, never by editing
  files. It must fetch the current settings immediately before writing,
  because an update replaces the whole document, and it must ask the user
  before switching a default to a broader setting (observed).
- The settings UI offers four modes: exclude chosen apps, exclude chosen
  sites, include only chosen apps, or include only chosen sites. Changes
  affect future history only (public docs).

**States and clearing.**
- Three states: running; paused, which keeps the current segment but records
  nothing new; and stopped, after which completed segments and memories stay
  in place (observed). A summary already in progress may still finish after a
  pause, and pause/resume is available in Settings and the menu bar (public
  docs).
- History can be cleared for the last 10 minutes, last hour, last day, or all
  time, and individual items can be deleted; the menu bar can clear the most
  recent session for an app. Clearing cannot be undone (public docs). The
  desktop status model carries matching clear windows (observed).

**Availability.**
- macOS desktop only; offered on Pro, Business and Enterprise plans. A
  workspace admin must first enable the feature, then each person opts in
  separately. Memories must be turned on, and it is unavailable when signed
  in with an API key or using Bedrock. The help page also addresses regional
  availability, naming the EEA, Switzerland and the UK. Off by default
  (public docs).
- The help page warns that app and site content can carry prompt injection,
  and recommends that users pause recording or leave sensitive apps out, and
  ask others for permission before their messages are recorded (public docs).

### Prompt surfaces (described, not quoted)

- **App-state tool description**: says the tool starts a session if needed,
  returns screenshot plus accessibility tree, and is mandatory once per turn
  before interacting.
- **Blocked-URL message**: ends the session and says the restriction holds even
  when the user navigates to the page themselves.
- **User-stop message**: tells the model to stop, acknowledge the stop in a
  final message, and offer to resume.
- **Missing-runtime message**: asks the model to retry a small, fixed number
  of times and then suggest relaunching the app.
- **Messages read-denied message**: forbids working around withheld content with
  CU or other tools unless the user explicitly asks.
- **CU skill**: tells the model to use only the REPL for computer interaction
  unless the user names another technique (AppleScript, osascript, JXA). It
  contains a confirmation policy with four tiers (hand off to the user, always
  confirm at action time, pre-approval is enough, no confirmation) and numbered
  categories. Examples: the final step of a password change is handed off;
  entering sensitive data in a form is treated as transmitting it.
- **REPL tool description**: requires the first invocation to make exactly one
  bootstrap call and nothing else. After a context summary, the model is told
  to call a documentation-refresh method to get the API docs back.
- **Activity summarizer prompt**: treats anything derived from observed content
  as untrusted, and that status survives quoting, summarizing or paraphrasing.
  Output is descriptive memory, never instructions.
- **Per-app notes** (e.g. Slack): advise setting the composer's value rather
  than typing, so a multi-line message is not sent by accident.

## Harness design takeaways

- **T1** — Keep dangerous powers in a small, independently signed helper.
  Screen capture, accessibility, Apple Events and lock-screen handling belong to
  a background app that updates on its own schedule. The agent side sees only a
  typed RPC, so OS permission grants survive frequent host-app releases.
- **T2** — Treat model code as untrusted and the service modules as trusted.
  A REPL cell can do nothing beyond `rpc(service, payload)` into a worker whose
  module paths are allowlisted, and it never sees the socket. Lesson for our
  harness: code-mode tools should receive capability handles, not raw file
  descriptors or environment variables.
- **T3** — Identify callers from signatures plus parentage, and name the relay
  case. An unsigned intermediary is acceptable only beneath a trusted signed
  ancestor. Returning one of three reasons (no parent, parent not trusted,
  relay lacking a trusted ancestor) makes failures easy to diagnose.
- **T4** — Check versions up front and give the model a recovery path. A
  mismatched handshake yields an error the model can relay, pointing at the
  human fix (restart the app). Errors pair a fixed numeric code with
  instruction-style text.
- **T5** — Block whole categories before asking anyone. Password managers,
  terminals, the host app and Safari are refused outright; managed
  requirements then set org-level allow/deny, default access and whether
  approvals may persist; only after that does the user see a per-app prompt
  with a risk level and an injection warning.
- **T6** — Deliver approvals as MCP elicitations that declare persistence. Each
  request states the allowed scopes (once, turn, session, always). A remembered
  answer skips the prompt and telemetry, and the tool clock stops while the
  user is deciding.
- **T7** — Freeze arguments once policy has passed (TOCTOU). Snapshot them into
  an immutable plain-data object containing the resolved app path, and hand
  only that snapshot onward.
- **T8** — Observe before acting, and send deltas. One fresh observation per
  turn is required before any action; actions reply with a short "done, look
  again"; tree changes arrive as incremental, cumulative or unchanged notices
  unless a full tree is requested; pixels are sent only on request.
- **T9** — Attach app-specific guidance at first contact, once. Markdown notes
  for an app ride along with that app's first observation in a session. Quirks
  such as Slack sending on Return are better placed there than in the global
  system prompt.
- **T10** — Make stop and interruption last for one turn, enforced via hooks. A
  user stop or takeover ends CU only until the next turn, and lifecycle hooks
  (Stop, SubagentStop, Interrupt) invoke a hidden end-of-turn tool so cleanup
  is deterministic.
- **T11** — Detect a human taking the controls. Debounced monitoring of real
  input counts as an intervention; the display is kept awake during work and
  focus is not stolen; physical input during auto-unlock aborts it and returns
  control.
- **T12** — Design non-idempotent connectors so retries are safe. Message
  sending consumes a one-time plan and leaves a tombstone, and the model is
  instructed to hold off on resending if delivery is unknown. Email, payments
  and posting tools should follow suit.
- **T13** — Bring documentation back after compaction. If a tool teaches its
  API through tool output, offer a call that re-emits the docs and tell the
  model to use it after a context summary.
- **T14** — Express the confirmation policy as a taxonomy inside the skill.
  Numbered categories, each tagged hand-off, confirm at action time,
  pre-approval suffices, or none, plus hygiene rules (avoid asking prematurely;
  content from third parties never counts as consent), are easy to version
  and review.
- **T15** — Memory built from observed activity stays descriptive and tainted.
  Summaries record what happened, never directives, and anything derived from
  untrusted content keeps that label through rewording.
- **T16** — Authenticate a local socket peer cryptographically, not by PID or
  path. Resolve the peer's code object from its audit token, read its signing
  team, and require it to match the broker's own team. This survives PID reuse
  and binary copying, and the same pattern works in both native and Node
  code paths.
- **T17** — Keep "is the direct peer trusted?" apart from "who is behind this
  call?". A fast signing check covers the immediate peer; a separate context
  records just the parent and the OS-responsible process with their signing
  identities, and failures carry a precise reason. Two named relations are
  easier to reason about than an open-ended ancestry walk.
- **T18** — Address synthetic input to one window of one process, never the
  global stream. Tagging events with the target window, positioning them in
  window coordinates, posting per process and faking activation/key focus lets
  the agent drive a background app while the human keeps the foreground and
  global hotkeys stay untouched.
- **T19** — Let each action optionally return the next observation. A flag on
  every actuator removes the mandatory observe round-trip after acting, while
  an explicit observe call remains available.
- **T20** — Give observation a cheap accessibility-only path. Refresh the tree
  (as a diff) by default, pay for a screenshot only when asked, and expose
  image compression quality so pixel cost is tunable.
- **T21** — Express hard target blocks as one pure, testable predicate over a
  stable app identifier, kept in code and below the org-policy and approval
  layers, so it can be unit-tested and cannot be loosened by configuration.
- **T22** — Treat resumption across a security boundary (here, the lock
  screen) as injected timing plus a lease broker. Timeouts and attempt caps
  are parameters rather than literals, attempts are single-use and bound to an
  active thread, physical input suppresses immediately, and the component that
  touches the sensitive field is kept minimal and unexported.
- **T23** — Keep raw evidence and distilled memory in separate layers. Raw
  events live briefly in segment logs with metadata; durable memory is
  Markdown at two time scales. Hand the agent a coarse-to-fine lookup order
  (long summaries, then short ones, then raw search).
- **T24** — Make staleness visible before history is trusted. A status call
  plus a comparison of the current time against the latest segment's
  timestamps tells the agent how current its view is.
- **T25** — Guard whole-document settings writes. Require a fresh read just
  before every replace, and require user confirmation before any default is
  widened.
- **T26** — Combine capture policies with AND across scopes (app and URL),
  let a block win inside a scope, and hard-code one exclusion (private
  browsing) that sits outside the rule system entirely.
- **T27** — Use passive history as a pointer, not an answer. When history
  identifies the app or document involved, switch to that authoritative
  connector or file instead of quoting observed text.
- **T28** — Shrink the sensitive layer. Raw data is kept for hours, users get
  clear windows and per-item deletion, and only summaries persist.

## Open questions

1. Partly answered: there is no multi-level walk; the direct peer must share
   the service's signing team, and a separate context records the parent and
   the responsible process (T16, T17). Still open: the exact boolean rule that
   combines the direct-peer check with the parent/responsible identities, and
   which signing identities beyond the vendor team count as trusted for a
   relay.
2. During auto-unlock, does the service type anything (e.g. an empty string)
   into the lock-screen field or only submit it? What does the plugin set in
   the authorization context to grant the unlock? Is installation strictly
   user-initiated, and where does the UI offer it?
3. What is the request/response shape of the server-side site-status check,
   and does a failed policy check default to fail-open or fail-closed?
4. The Computer History summarizer is a short-lived agent session whose
   input is processed server-side (public docs), and what is captured is now
   known (interaction events plus accessibility context, no screenshots).
   Still open: is that session sandboxed more tightly than a normal agent
   run, and which model backs it?
5. How do the Apple Event/XPC transport and the JSON-RPC socket relate? Is the Apple Event/XPC path a leftover, or reserved for the desktop's native
   bridge and the older MCP client?
6. The per-episode auto-unlock attempt cap and the four lock-screen timing
   values are injected at construction (T22). What are their default values,
   and can the cap be changed remotely through the vendor's experiment
   system?
7. How does the alternative object-style CU API in the REPL differ from the
   method-style one, and which does the current unified CU plugin expose by
   default?
8. Is there any per-action pacing beyond settle delays and approvals, for
   example on the server side? The local service has no rate limiter, so any
   limit would have to live elsewhere.
9. What triggers Computer History summarization, how often, and with what
   token budget? The docs are silent; only the file naming is known.
10. What goes into the suppressed-events file: excluded events retained for
    audit, or only markers recording that something was dropped?
11. How are Computer History memories weighted against other memories when
    memories are injected into a chat?
