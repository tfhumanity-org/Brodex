# 09 — Browser and computer use (Claude Code, public docs 2026-10)

## Overview
GUI interaction is offered through a ladder of increasingly general (and
slower, riskier) channels, and the agent is told to prefer the most precise
one: MCP connector → shell → browser automation → full computer use. Browser
automation comes from the **Claude in Chrome** extension (bridged to the CLI
over native messaging, exposed as a built-in MCP server) or the desktop app's
own browser pane (`08`). **Computer use** (research preview, Pro/Max) is a
built-in `computer-use` MCP server that screenshots and drives native apps,
with per-app approval and several hard guardrails. The desktop app also has
a dedicated iOS Simulator pane.

## Diagram
```
task needs GUI?
  ├─ connector/MCP exists? ─► use it
  ├─ shell can do it?       ─► Bash
  ├─ web task + Chrome ext? ─► claude-in-chrome (user's real browser, own tab group)
  │                            or desktop browser pane (clean profile)
  └─ else                   ─► computer-use MCP (screenshots + input on approved apps)

claude-in-chrome:  CLI ⇄ native-messaging host ⇄ extension (service worker) ⇄ tabs
computer-use:      lock (1 session) → per-app approval → hide other apps →
                   screenshot (downscaled) → click/type → … → unhide on turn end
```

## Mechanics

### Claude in Chrome (CLI / VS Code)
- Requires the extension (Chrome, Edge, other Chromium browsers; not WSL), a
  claude.ai login (not API key / long-lived token / third-party provider).
  `claude --chrome`, `/chrome` for status, reconnect, browser selection, and
  "enabled by default".
- Uses the user's real browser and login state; opens its own tabs grouped
  per session; group closed on `/clear` (with nuance on resume/exit).
- Capabilities: navigate, read page / accessibility tree, find, click/type,
  form input, console and network reading, JS execution, file upload (≤10 MB
  total; refuses multi-hard-link files and credential-looking paths such as
  `.env`, `.pem`, `.key`, `.ssh/`), GIF recording, screenshots to disk,
  batching, shortcuts.
- Pauses for logins and CAPTCHAs and asks the user to handle them.
- Site permissions come from the extension; auto-mode classifier approval
  can stand in for the extension's per-site check unless rules deny.
- Plan mode prompts before GIF recording, new tabs, shortcuts.
- A project's settings cannot turn on browser integration (only the user can).
- Native messaging host manifest installed per browser.
- Enabling by default costs context (tools + instructions always loaded).

### Desktop browser pane
See `08`: clean profile, per-site approval for external pages, localhost
trusted, classifier review of write actions, admin kill switches.

### Computer use
- macOS (CLI) / macOS + Windows (desktop); Pro/Max only; interactive only
  (not `-p`); not with third-party providers. Enabled via `/mcp` (CLI) or a
  desktop setting; needs Accessibility + Screen Recording permissions.
- **Per-app, per-session approval**; prompt lists apps, extra permissions
  (e.g. clipboard), and how many apps will be hidden. Warnings for apps that
  are effectively shell access (terminals, IDEs), any-file access (Finder),
  or system settings.
- **Control tiers by app category**: browsers and trading platforms view-only;
  terminals/IDEs click-only; everything else full control. Desktop adds a
  denied-apps list; approvals last the session (30 min for phone-dispatched
  sessions).
- One session at a time (lock held until session exit; stale locks freed when
  the holder process is gone).
- Other apps hidden while working; restored at turn end. The terminal
  running Claude is excluded from screenshots so the model never sees its own
  UI/prompts.
- Screenshots downscaled automatically (e.g. 3456×2234 → ~1372×887); no
  setting.
- Global `Esc` aborts immediately and the keypress is consumed (so injected
  content can't use Esc to dismiss dialogs); OS notification on start/finish.
- Each action is checked and on-screen prompt-injection is flagged.

### iOS Simulator (desktop)
Dedicated pane and tools for building/launching apps in the simulator,
screenshots, taps/swipes/text, deep links — preferred over generic computer
use for iOS work. (Observed in use; doc page exists.)

## Harness design takeaways
- **T1** — Encode a *tool preference ladder* (API → shell → browser → screen)
  so the most precise, auditable channel is used first.
- **T2** — Separate "agent's own browser" (clean profile) from "user's
  browser" (real logins) and permission them differently.
- **T3** — Per-app approval with capability tiers by app category limits
  blast radius better than all-or-nothing screen control.
- **T4** — Hide the harness's own UI from the model's screenshots and reserve
  an unspoofable abort key.
- **T5** — Single-session lock for the physical screen; never let two agents
  fight over the mouse.
- **T6** — Refuse credential-shaped files in upload flows and stop at
  logins/CAPTCHAs for the human.
- **T7** — Downscale screenshots in the harness so the model sees a stable
  coordinate space regardless of display DPI.

## Open questions
- Coordinate-space contract (scaled vs. native) for click actions in
  computer use. (uncertain)
- Whether Chrome actions pass through the same auto-mode classifier as shell
  actions in all hosts.
