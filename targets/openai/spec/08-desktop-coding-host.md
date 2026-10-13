# 08 — Desktop coding host layer  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

The desktop app is a graphical host wrapped around `codex app-server`. The
headless app-server owns the agent loop, threads, sandboxed command execution
and environments. The desktop's main process handles whatever has to touch the local machine or the UI: interactive terminals, a git helper that manages
worktrees and per-turn diffs, connections to other hosts (WSL, SSH, remote
control, cloud environments), "open in" editor integrations, an in-app browser
panel, notifications, and the native bridge used by Computer Use (see 09). The
renderer is built on an in-house Chromium-based runtime that exposes an
Electron-compatible API, so a few artifacts (such as the AppleScript
dictionary) are inherited from Chromium rather than designed for agents.

## Diagram

```
┌──────────────────────── renderer (React UI) ─────────────────────────┐
│ thread view · diff panel · terminal panel · review · env editor       │
│ worktree settings · browser panel · pull-request flow                 │
└──────────────┬────────────────────────────────────────┬───────────────┘
               │ preload bridge                         │ IPC
┌──────────────▼──────────── desktop main process ──────▼───────────────┐
│ app-server manager ── stdio | websocket ──▶ codex app-server           │
│                       (local / WSL / SSH / remote-control host)        │
│ terminal manager ── local PTY | app-server process session | tunnelled │
│                     SSH (cloud env, SSH over QUIC via MASQUE proxy)    │
│ git helper ── worktree add/remove, tree snapshots, apply --3way,       │
│               private ref namespace for snapshots / diffs / handoff    │
│ open-in targets (VS Code family, JetBrains, Xcode, Zed, terminals…)    │
│ remote control: hardware-bound device key, enrollment, keep-awake      │
│ browser panel ⇄ browser-automation service (authenticated local pipe)  │
│ notifications · badge · dock icon helper · power-save blockers         │
│ host-services socket ⇄ JS REPL tool (Computer Use, browser)            │
│ bundled app-tools MCP server (thread/automation meta-tools)            │
└────────────────────────────────────────────────────────────────────────┘
```

## Mechanics

### App-server process and hosts

- The local app-server is launched as a child process. Environment-derived
  settings are passed as `-c key=value` overrides, logging is set to JSON at
  warning level, and the transport is either stdio or a websocket. A daemon
  mode is probed first with a version check that has a 2.5 s timeout. On
  Windows the server can run inside WSL.
- Four host kinds exist: local, WSL, SSH and remote-control. Every terminal,
  git, open-in and handoff operation takes a host id and resolves that host's
  connection and config before acting.
- Desktop-only features reach the app-server through extension methods such
  as file metadata lookup, environment info, and one-shot command execution.
- The desktop appears never to run agent shell commands itself. All agent
  execution stays sandboxed inside the app-server. The desktop only spawns
  processes the user drives directly (PTYs, git for the UI, editor launchers).

### Terminals

| Backend | Used for | How |
|---|---|---|
| Local | local / WSL threads | PTY running the user's preferred shell; WSL gets the distro and starting directory |
| Remote host | SSH and remote-control hosts | app-server process session with a TTY, streamed stdout/stderr |
| Cloud environment | cloud tasks | SSH session with forwarding disabled that changes to the thread's directory and starts a login shell, labelled "SSH over QUIC" |

- All three sit behind one interface: write, resize, terminate, dispose.
- Output that arrives before the UI registers the session is buffered, keeping
  only the last 16,000 characters.
- The terminal environment sets the conversation title, sets a 256-colour
  xterm `TERM`, and strips `TERMINFO`. Local sessions also get the worktree's
  captured setup environment (see Local environments).
- Open terminals, pending terminal creation, active downloads and voice
  sessions each block an automatic update or relaunch until they finish.
- Agent background processes can appear as terminal tabs in the UI.

### Cloud environment terminal (SSH over QUIC)

- Before connecting, the desktop uses the app-server's command execution to
  read the remote username and the environment's SSH host public key. The host
  key is therefore pinned through a channel that is already authenticated.
- The QUIC tunnel is driven by the bundled codex CLI acting as a MASQUE client,
  given a proxy URL, a target address and an auth token. The proxy origin must
  appear in an allowlist file shipped with the app, and each destination is
  checked before connecting. There are UI states for connection failure,
  connection closed, and credential renewal.
- In effect, a cloud task gains an interactive login shell, yet raw SSH is
  never exposed and no host key is trusted on first use.

### Worktrees and local environments

Worktree creation:

1. Resolve the starting ref.
2. When branching from the working tree, collect untracked files and the
   current uncommitted diff.
3. Add a detached worktree at that ref.
4. Apply the uncommitted diff and untracked files inside the new worktree.
5. Stream git output to the UI as logs.
6. Restore runs with terminal prompts disabled. If the add fails, the
   half-made worktree is force-removed.

User-facing worktree settings include a worktree root directory, automatic
cleanup, and a number of worktrees to keep.

A **local environment** is declared in `environment.toml`:

| Field | Meaning |
|---|---|
| `setup` | setup script, with optional per-platform overrides (macOS / Linux / Windows) |
| `cleanup` | optional teardown script |
| `actions[]` | named one-click project actions with an icon (tool / run / debug / test), a command, and an optional platform |

- Setup and cleanup scripts receive the source tree path and the worktree path
  as environment variables (`CODEX_SOURCE_TREE_PATH`, `CODEX_WORKTREE_PATH`),
  with colour output forced on.
- When setup succeeds, the shell environment it produced is captured as a
  `{set, exclude}` delta (ignoring volatile variables such as `PWD`, `OLDPWD`,
  `SHLVL`) and stored per worktree. Status, log and timing are reported back to
  the UI.
- A per-repo "synced branch" record (branch plus last synced tree) is kept as
  JSON inside the repository's git directory.
- Local environments appear to give the desktop the same model as cloud
  environments: a declarative setup whose resulting environment is re-applied
  to each terminal and agent shell opened in that worktree.

### Turn diffs, snapshots, undo

- The git helper keeps three kinds of private refs: per-turn baseline
  captures, per-thread checkpoints keyed by hashes of a checkpoint key and the
  turn id, and general snapshots. They live under a dedicated namespace inside
  `refs/`, separate from branches and tags.
- At turn start it records the worktree's **tree hash**, including untracked
  files. It does this with a temporary index file, `add -A` and `write-tree`, so
  the real index stays untouched. Captures expire after about 7 days.
  The status scan uses porcelain v1 output with renames off, capped at 1 MiB
  and 5 s.
- A turn diff is a tree-to-tree comparison between the baseline capture and
  the current (or next turn's) tree. Checkpoints chain turns together within a
  thread.
- Apply and revert use `git apply`, optionally reversed, binary-aware, 3-way,
  and scoped either to the index only or to index plus working tree. Stage and
  unstage use `add -u` / `reset --mixed`. Stash push/pop is also available.
- The UI offers a diff panel, inline per-file review, line comments, diff
  stats, checkpoints/undo, commit, branch switching and pull-request creation.
- Line comments on diffs are likely handed to the agent as structured review
  feedback.

### Cross-host thread handoff

- The model-facing `handoff_thread` tool moves a thread between hosts or
  worktrees. It runs four named stages: prepare the destination host, transfer
  artifacts, create a new worktree, apply the changes.
- Two artifacts move: a git bundle of the source head plus its changes, and the
  thread's rollout JSONL transcript. Temporary source and destination refs are
  created for the transfer. Distinct errors cover "source not in a repository"
  and "destination not in a repository".
- A thread is therefore just data, a transcript and a git delta, so relocating
  it means replaying both rather than migrating a live process.

### "Open in" / editor integration

- Targets include Xcode, Zed, VS Code, Cursor, Windsurf, the JetBrains IDEs
  (IntelliJ, PyCharm, WebStorm, GoLand), and the Terminal, Ghostty and Warp
  terminals, plus a few document apps found
  by bundle id. Each target has per-platform detect, argument-building and
  open steps.
- VS Code-family editors open at `path:line:col`. For SSH hosts they use the
  editor's remote mode with a `vscode-remote://` folder URI. Linux terminals
  receive their own working-directory flags. Remote-control hosts cannot be
  opened this way.
- The preferred target is remembered. File references in agent output are
  parsed as `path:line:col` so they become clickable.

### Remote control

- The app-server can enrol with a relay over a websocket, pair, and recover
  from auth failures.
- On the desktop, a native module creates, reads, signs with and deletes a
  device key. It is ECDSA P-256 held in the Secure Enclave, with a fallback to
  an OS-protected non-extractable key. Signatures use a versioned,
  domain-separated payload.
- The desktop handles client enrollment (including a step-up token), connection
  authorization, listing and renaming remote environments, and explicit
  "enrollment incomplete" / "key material missing" states. An option keeps the
  machine awake for remote control while on AC power.
- This appears to cover two roles: this Mac's app-server dialling out so it can
  be driven from other devices, and this desktop acting as a client that connects
  to other enrolled environments and authenticates with the hardware-bound key.

### In-app browser panel

- Roughly 95 IPC channels cover page loading, tab lifecycle, screenshots, find,
  automation state (viewport, cursor, capture surface), comment overlays,
  design-tweak overlays, creating comments from a selection, and image
  attachments.
- The page preload asks synchronously for a WebMCP policy. The main process
  bridges Chrome DevTools Protocol events, downloads, mouse movement and WebMCP
  tool calls to a browser-automation service. That service's local pipe peer is
  authenticated by code signature (same approach as 09).
- Thread configuration tells the model to prefer the dedicated browser plugin
  for driving the in-app browser or Chrome, and to fall back to Computer Use
  only when the user asks for it.
- The panel appears to be both a preview for local dev servers and an
  annotation tool. Clicked elements, comments and design tweaks become agent
  input along with screenshots.

### App-tools MCP (desktop as a tool provider)

- A bundled plugin runs an MCP server that calls back into the desktop over a
  local pipe. Its thread and automation meta-tools (`fork_thread`,
  `create_thread`, `handoff_thread`, a tool that posts a message into another thread,
  `automation_update`) use the `prompt` approval mode. Its other tools are
  approved automatically. The tools are excluded from deferred tool search,
  and the tool timeout is 3600 s.
- When these gated tools are disabled, the desktop removes them from the
  model's tool list entirely.
- A second bundled plugin is a GitHub pull-request MCP App with its own HTML
  UI. It receives the GitHub token via env passthrough and can review PRs
  without a local checkout.

### Notifications, badge, dock

| Kind | Behaviour |
|---|---|
| permission | never times out |
| question | never times out |
| turn complete | can carry an inline reply field |
| completed | standard |

- Up to 4 action buttons. IDs are de-duplicated over the last 1000.
  Notifications are suppressed by kind and window-focus state. Custom sounds
  are supported.
- The app sets a dock badge count. Power-save blockers can prevent sleep
  (optionally display sleep) during active work or remote control.
- A small launch-services helper resolves apps by bundle id, lists URL
  handlers, signals finished downloads, switches the dock icon (several icon
  choices), and repairs a stale dock tile left over from the Codex-to-ChatGPT
  rename.
- Other native modules handle a double-Command hotkey (screenshot attach,
  dictation), audio level metering, headset mute, HID changes, WebAuthn over
  USB, app integrity attestation, and Sparkle auto-update.

### AppleScript

- The scripting dictionary is the stock Chromium one: windows, tabs, run
  JavaScript in a tab, back/forward, reload, view source, copy/paste. There
  are no thread or prompt commands.
- It is likely an artifact of the Chromium runtime rather than an intended
  automation surface. Computer Use also forbids the ChatGPT/Codex apps
  themselves, so the agent has no route to automate its own host through CU
  either.

## Harness design takeaways

- **T1** — Separate the agent core from the host shell. Put execution and
  thread state in a headless server with a stable RPC, and let the GUI add
  user-facing affordances plus a few extension methods (metadata, exec, env
  info). That lets one core serve local, WSL, SSH and cloud hosts unchanged.
- **T2** — Make the host id a parameter on every operation. Terminals, git,
  open-in and handoff all take one. Backends (local PTY, remote process
  session, tunnelled SSH) share a single write/resize/terminate/dispose
  interface.
- **T3** — Take snapshots with a private index and private refs. Record turn
  baselines as tree hashes using a throwaway index and `write-tree`, store them
  under a dedicated ref namespace, and expire them. Per-turn diffs and undo then
  leave the user's index, stash and branch list alone.
- **T4** — Undo by applying a reverse patch, not by resetting. A reversed
  3-way apply, scoped to staged or unstaged changes, copes with concurrent user
  edits far better than a hard reset.
- **T5** — Give each task its own worktree that carries over uncommitted work.
  Create detached worktrees from a ref, replay the uncommitted diff and
  untracked files into them, and bound disk usage with keep-count and
  auto-cleanup.
- **T6** — Define the environment once and reuse it in every shell. A repo-level
  setup script runs per worktree, the environment delta it produces is
  captured and injected into each terminal and agent shell, and project
  actions live in the same file.
- **T7** — A thread can move as transcript plus git bundle. Handoff copies both
  through named, resumable stages using temporary refs. This is a simple
  migration protocol that is easy to audit.
- **T8** — Gate side-effecting meta-tools by name. Tools that create, steer or
  fork threads or change automations need a prompt by default and disappear
  from the tool list when disabled.
- **T9** — Pin the channel to remote environments. Use an allowlisted proxy
  origin plus a per-destination check, and fetch the host key over the
  already-authenticated control channel. Avoid ad-hoc SSH trust.
- **T10** — Bind remote-control identity to hardware. A Secure Enclave key with
  an OS-protected fallback, a versioned domain-separated signing payload,
  step-up tokens for enrollment, and explicit revoked/incomplete states.
- **T11** — Use notifications as the human-in-the-loop channel. Notifications
  for permissions and questions stay until handled, turn-complete ones accept
  an inline reply, and de-duplication plus focus-aware suppression keep the noise down.
  Approvals can be asynchronous without being lost.
- **T12** — Let long-running work hold off disruptive restarts. Subsystems
  report restart blockers (terminals, downloads, voice) so auto-update waits,
  and power-save blockers keep the machine awake during turns or remote
  control.
- **T13** — Treat the preview browser as an input device. Structured comments
  and design tweaks with screenshots become agent input, and dedicated browser
  control is preferred over generic Computer Use.
- **T14** — "Open in" should know about locations and remotes. Hand the
  editor a `path:line:col` location and switch it into remote mode for SSH
  hosts, so users don't have to hunt for files.

## Open questions

1. Where in the repository does `environment.toml` live (one file or one per
   named environment)? What is the full schema, including platform-specific
   env and secrets?
2. What exactly keys a turn-diff checkpoint (thread, working directory, or
   both)? How are multi-turn diffs combined when the user edits between turns?
3. Are the private snapshot/handoff refs ever pushed or fetched, and does the
   push path filter them out?
4. Does remote control use the same relay for "this Mac as an environment" and
   "desktop as a client"? Which values (account, client id, nonce or
   others) does the device-key signature cover?
5. Which CLI entry point implements the MASQUE tunnel, and is its auth token
   minted per environment or per session?
6. What does the browser panel's WebMCP policy allow, and how are tools
   provided by a page shown to the model (naming, approval)?
7. Does the Chromium AppleScript command for running JavaScript work against
   in-app browser windows? If so, is there a Chrome-style opt-in for
   JavaScript sent via Apple Events?
