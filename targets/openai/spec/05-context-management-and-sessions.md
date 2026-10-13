# 05 — Context management & sessions  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview
The harness keeps memory at three tiers, each with its own lifetime. The first is in-context history,
which compaction or explicit windowing keeps bounded. The second is a durable record of each thread:
an append-only JSON Lines "rollout" file that acts as the source of truth, plus a SQLite index derived
from it, which together support resume, fork, paging and search. The third is a distilled memory folder
shared across threads, built by a background pipeline in two phases. The desktop app and the CLI talk to
the same app-server over JSON-RPC. That server owns the turn loop, writes rollouts, maintains the
index, and also runs goals, the message queue and managed git worktrees.

## Diagram
```
 Desktop UI / TUI / CLI  --JSON-RPC-->  app-server
                                          |
        thread start/resume/fork/compact, queue, goals, memory mode, turn start/steer/interrupt
                                          v
                              session turn loop ----> Responses API
                                |   ^
           pre-sampling check --+   +-- post-turn check (threshold %)
                                |
              +-----------------+------------------+
              | provider-native compaction         | local summariser fallback
              | (one opaque, encrypted item        | (handoff-summary prompt; on
              |  replaces the earlier history)     |  overflow drop the oldest item)
              +-----------------+------------------+
                                v
            rollout recorder (one writer lock per thread)
                                |
                     sessions/<date>/rollout file (JSON Lines, append-only, later zstd)
                                |  tailed incrementally by byte offset
                                v
                SQLite state DB: threads, turns, items, queue, goals, spawn edges, projects
                                |
       memories pipeline (startup, idle threads only):
         phase 1: per-rollout extractor -> raw memory + summary + slug
         phase 2: global consolidation sub-agent -> memory folder
                  (summary file, handbook, per-rollout recaps, skills)
                  -> summary file injected into later sessions
```

## Mechanics

### Auto-compaction
- **Two backends.** There is a local summariser and a provider-native ("remote") compaction path.
  In the remote path the server is expected to return exactly one compaction item. That item carries
  encrypted content and replaces the prior history, so the client never sees a plaintext summary. It
  appears the client sends a trigger item and the model or server produces the opaque replacement.
- **Fallback trimming.** If local compaction itself overflows the context window, the oldest history
  item is removed and the attempt is repeated.
- **Two trigger points.** Compaction can run before sampling, when the next request would be too large,
  and after a turn, when usage passes a configurable percentage threshold that must lie between 0 and
  100. If post-turn compaction fails, the completed turn is kept.
- **Settings.** `model_context_window` and `model_auto_compact_token_limit` are user-facing. There is
  also a scope switch that counts either the total prompt or only the body after the static prefix.
  This likely exists so that cached instructions and tool definitions do not trigger compaction
  early. The compaction prompt can be overridden by the user.
- **Model catalog.** Each model entry declares a context window (272,000 tokens in one entry, with a
  larger maximum of 872,000), an optional auto-compact limit (often null, which likely means "derive it
  from an effective-window percentage"), a tool-output truncation policy, and a compatibility hash for
  compaction blobs. The hash appears to let a model switch force re-compaction when an opaque blob
  would not work with the new model.
- **Hooks and UI.** Pre-compact and post-compact lifecycle hooks are available. The app-server exposes
  a manual compact method and sends a "context compacted" notification. After compaction the user sees
  a one-time warning that long threads and repeated compactions can make the model less accurate, and
  that a fresh thread is better.
- **Persistence.** The rollout records a compaction entry with the replacement history, window ids
  (first, previous, current) and the response id that produced it. On resume, the harness likely starts from
  the most recent replacement history rather than parsing the whole log again.
- **Reviewer reuse.** The auto-review sub-agent can reuse its parent's compacted history, up to a token
  cap.

### Compaction prompt (described)
The local summariser prompt casts the model as writing a checkpoint handoff for another model that will
continue the task. It asks for a short summary covering four things: progress so far and key decisions,
constraints and user preferences, the remaining work, and the critical data or references needed to
continue. When the summary is injected back into context, a short preamble frames it as notes that a
previous model produced partway through the task.

### Experimental alternative: model-driven context windows
This mode ships disabled for every model in the catalog.
- The model does not get summarised. It maintains its own checkpoint through a notes tool, then calls a
  tool that starts a fresh context window. A context-window fragment in the developer context tells it
  the first, previous and current window ids.
- Recovery tools are read-only. A history tool can list windows, list items, read an item by id and
  search contents. The notes tool can list, read, search, append and write. Every non-assistant item is
  tagged with an id the model can cite. Notes are stored per agent under a virtual path, other agents can
  read them, each file is capped at 1,000,000 bytes, and the prompt tells the model to treat them as
  private and never disclose them.
- A reminder is injected when about 6,144 tokens remain. A fallback buffer of 16,384 tokens drives a
  forced sequence: one write or append to notes, then a new window, with no other tools allowed.
- There is also a separate session-wide cost budget. It counts "weighted tokens" and weights sampled
  output differently from prefill. A budget tag reports what remains, and exceeding the budget is a
  distinct error. This appears to be shared across the whole agent tree.
- The developer context is built from tagged fragments: permissions, model switch, managed
  instructions, apps, collaboration mode, multi-agent role and mode, environments, git attribution,
  plugins, realtime, skills, tools, personality, token budget, context window and its guidance, and the
  session budget.

### Tool-output truncation
- Each model has a truncation policy measured in tokens or bytes (10,000 tokens in the observed entries),
  and a global config key sets the tool-output token limit.
- Truncation cuts out the middle and leaves an explicit marker giving how many chars or tokens were
  removed.
- Shell results are framed with the exit code, wall time, total output lines, a chunk id, the session id
  of a process that is still running, and the original token count when output was cut.
- The `exec_command` and `write_stdin` tools accept `max_output_tokens` (default 10,000, which policy may
  lower further) and `yield_time_ms`. The truncation policy also appears to be sent to the backend.

### Rollout files
- Each line is an object with a timestamp, a type and a payload. Entry types include session metadata,
  response items, inter-agent messages and their metadata, compaction records, turn context, token-usage
  records, environment ("world state") snapshots, event messages and realtime items, plus a few
  verification and risk-scoring records.
- Session metadata records where a fork came from (the source thread and the ordinal it was cut at),
  which client created the session and with which CLI version, any dynamic tools, the memory and
  history modes, and the multi-agent generation in use.
- **Paginated history mode.** Forks and sub-agents appear to point at the parent's rollout up to a
  given ordinal instead of copying it, and they append only their own items. On read, the inherited prefix is
  checked for completeness. Older threads default to a legacy copy mode.
- Environment state is stored as a full snapshot followed by patches. A patch that arrives without a
  prior snapshot is ignored. This likely keeps per-turn context deltas small and stable for caching.
- Rollouts are compressed with zstd by a background worker that takes a maintenance lock. A reverse scan
  reads recent lines efficiently. Active threads live under `sessions/` and archived ones under
  `archived_sessions/` in the Codex home directory. A doctor check confirms this split and can backfill
  the index from rollout files.

### State DB (projection over rollouts)
- SQLite with embedded migrations. Thread rows hold the rollout path, source, provider, cwd, title,
  sandbox and approval settings, tokens used, git info, first user message, agent nickname, role and
  path, memory mode, model, reasoning effort, preview, recency, pin and section, project, and creator.
- Turn rows store the rollout ordinal range and byte-offset range, status, duration, and the first user
  item and final agent item. Item rows store serialized items. A per-thread projection cursor holds the
  next byte offset and ordinal to process.
- Other tables cover queued items (with a trigger that bumps a global revision), goals and goal
  continuation deferrals, parent/child spawn edges with status, dynamic tools, projects, sections,
  attachments and remote-control enrollments.
- The DB appears to be an incremental, rebuildable index. It tails each rollout from the stored offset
  so that turns and items can be paged without re-parsing the file, while the rollout stays
  authoritative.

### Concurrency
- Each thread has a writer lock file, managed under a coordination lock that also cleans up stale locks.
  Deleting a thread stops its writer first.
- A process-level guard stops a second Codex instance from opening the same local data.
- A session index (JSON Lines) sits beside the lock directory. It likely maps ids to names and paths
  for `codex resume <name>` and fast picker listing.

### Resume, fork, queue, steer
- CLI: `codex resume` and `codex fork` open a picker by default or take `--last`. `codex queue` adds a
  message to an existing session. `codex archive`, `unarchive` and `delete` are also available. The
  `--worktree` flag works for new sessions, fork, and `exec` (including fork via exec), but not resume.
- App-server thread methods cover start, resume, fork, read, list, archive, delete, revert, item
  injection, compaction, memory mode, goals, queue management (add, list, update, delete, reorder,
  start), turn and item paging, search, and background terminals. Turn methods are start, steer and
  interrupt. Notifications report queue changes, token usage and goal updates.
- Revert rewinds the conversation only and leaves file changes alone. Resuming with a different model is
  logged.
- Only user input can steer a running turn. A turn can be started by user input or by a standalone
  function-call output.
- In the desktop app, starting, steering, adding a message to or queueing on a thread blocks that
  thread's auto-archive.

### Goals
- Goal tools (create, update, get) work only on persistent threads. Status values are active, paused,
  blocked, usage-limited, budget-limited and complete. The model may set only complete, blocked, or
  paused (and paused only when the user asks).
- Prompt templates cover continuation, hitting the budget limit, and an updated objective. The objective
  is wrapped as untrusted data, and token usage, budget and remaining tokens are injected next to it.
- When a turn ends with a goal still active, the server appears to start another turn automatically
  using the continuation prompt. The client can defer this, and the continuation is skipped when
  automatic idle work is refused.
- The continuation prompt states that a goal outlasts any single turn and should not be shrunk to fit
  the current one. Completion needs an audit that positively demonstrates the goal is done, not just
  the absence of obvious remaining work. The model may mark a goal blocked only after the same blocker
  recurs on 3 consecutive goal turns.

### Memories
- Config covers version, dual-write, generate and use toggles, dedicated tools, limits on raw memories
  for consolidation, unused days, rollout age, rollouts processed per startup, minimum idle hours and
  minimum remaining rate limit, and the models used for extraction and consolidation. Another option
  turns memories off for threads that used MCP or web search.
- A thread's memory mode can be enabled or "polluted". Threads that took in external content appear to
  be marked polluted and left out of memory generation, which acts as an injection firewall for
  long-term memory.
- **Phase 1** runs one extractor per rollout. It outputs a rollout summary, a slug and a raw memory, or
  explicitly does nothing. Its gate question asks whether a future agent would actually act better
  because of the note, and it favours saving the user's time over saving the agent's. Jobs are
  claimed through leases with ownership tokens, retry counts and watermarks.
- **Phase 2** is a single global consolidation sub-agent that edits a workspace folder. It keeps
  ownership through heartbeats, writes a diff of the workspace, refuses symlinks, and can be vetoed by
  a Stop hook.
- **Folder layout:** a summary file that is always loaded into the system prompt and carries a version
  marker on its first line; a grep-able handbook (`MEMORY.md`); a temporary raw-memories file;
  `skills/<name>/SKILL.md`; and per-rollout recap files named by slug.
- **Read path:** an injected Memory section says when to consult memory and when to skip it. It caps a
  quick pass at roughly 4–6 search steps, tells the model not to reopen the summary it already has, and
  says to verify a memory when that is cheap and to mention possible staleness when it is not. Dedicated
  tools list, read and search memory, and add an append-only ad-hoc note, but only when the user
  explicitly asks to remember, forget or update something. Notes are named with a date prefix and a slug.
- Answers can cite memories by rollout id and line range. The app-server offers memory status, reset
  and per-thread memory mode.

### Managed worktrees
- Worktrees are placed in buckets and have owner files, so one worktree cannot belong to two threads.
  Spotlight indexing is turned off on the worktree root.
- If the source conversation changes while a worktree is being created, the user gets the path of the
  orphaned checkout and the `git worktree remove` command to clean it up.
- Desktop settings cover the worktree root, how upstream is refreshed (never or best effort),
  auto-cleanup (on by default) and a keep count (default 15). Pinned threads are exempt from cleanup.
  The path of the local environment config is copied into the worktree's git config.
- The model-facing guidance prefers reusing the current checkout. It describes tools to create, archive
  and restore worktrees and to list artifacts.

## Harness design takeaways
- **T1** — Make the append-only rollout the single source of truth and treat SQLite as a projection you
  can rebuild. Storing byte offsets and ordinals for each turn lets the UI page from the DB while
  resume, fork and backfill re-derive state from the log, and a per-thread cursor keeps indexing
  incremental.
- **T2** — Put two compaction backends behind one interface. Prefer the provider's opaque compaction,
  keyed by a model compatibility hash, with a local summarising fallback that discards the earliest
  item when it overflows. Write the replacement history into the rollout so that resuming never compacts again.
- **T3** — Use separate triggers before sampling and after a turn, plus a scope switch (total versus
  body after prefix), so a large cached static prefix does not cause premature compaction.
- **T4** — Keep the handoff prompt small and generic: progress and decisions, constraints and
  preferences, remaining work, critical references. Frame the injected summary as written by an earlier
  model, and show the user a one-time warning that long threads lose accuracy.
- **T5** — Consider letting the model manage its own windows instead of summarising. Give it a
  size-capped notes store namespaced per agent, read-only history access by item id, and a
  start-new-window tool. Tag items with ids, remind the model at a set number of remaining tokens, and
  keep a hard fallback that allows only "save notes, then roll over".
- **T6** — Truncate tool output by tokens from the middle, with explicit markers and metadata (original
  count, total lines, chunk id, id of a still-running session). Callers may request an output size, but
  policy caps it.
- **T7** — Use a writer lock per thread and one process-level data lock, with stale locks cleaned up
  under a coordination lock. This becomes necessary as soon as the desktop UI, the CLI,
  automations and sub-agents may each write to one thread.
- **T8** — Have forks and sub-agents point to parent history by ordinal instead of copying it, and check
  the inherited prefix when reading.
- **T9** — Store environment context as a snapshot followed by patches, so that turn-context deltas stay
  small and cache-stable.
- **T10** — Model persistent goals as first-class records that hold the objective, a token budget, elapsed
  time, and a status set where some transitions belong to the system only. Add automatic continuation turns and strict
  completion and blocked audits (blocked only after 3 identical blockers in a row), and treat the
  objective as untrusted.
- **T11** — Build memory in two phases. Phase 1 is cheap per-rollout extraction behind a strong no-op
  gate. Phase 2 is a leased global consolidation sub-agent that writes a folder in layers of detail:
  an always-loaded summary, then a grep-able handbook, then per-rollout recaps, then skills. Skip
  extraction for threads polluted by external content so injected text cannot persist.
- **T12** — Spell out how memory should be read: when to skip it, a search budget of about 4–6 steps,
  no reopening of the injected summary, verifying when cheap and noting staleness when not.
- **T13** — Store the message queue as DB rows with a revision counter that only goes up, so several
  clients can watch and reorder queued follow-ups consistently.
- **T14** — Give managed worktrees ownership files, keep-count garbage collection (default 15) with
  pinned-thread exemptions, and explicit recovery steps when a checkout ends up orphaned.

## Open questions
- What effective auto-compact threshold applies when the catalog limit is null? It is probably a
  percentage of the context window, but the value could not be found statically.
- Is remote compaction a separate endpoint or a trigger item sent in-stream on the normal request?
- What is the record schema of the session index file?
- How are rollout files named and bucketed by date in this build? The current description is carried
  over from earlier versions and not confirmed here.
- What do the verification and delivered-answer rollout records mean? They are probably reviewer
  bookkeeping, but this was not traced.
- How often does memory consolidation run (only at startup, or periodically), and what are the default
  per-startup and idle-hour limits?
