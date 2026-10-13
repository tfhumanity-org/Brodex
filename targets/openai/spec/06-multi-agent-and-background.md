# 06 — Multi-agent & background work  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview
Codex lets one root agent hand work to a tree of sub-agents. Each sub-agent is a full thread with its own
rollout, but the parent's turn owns it. Two generations of collaboration tools ship together, and the
model catalog chooses one per model. The newer generation addresses agents by hierarchical path and
returns results as mailbox messages instead of blocking tool returns. Several mechanisms sit around the
tree. Long-running commands run as PTY sessions that the model polls. Optional cloud tasks can run
several attempts and pick the best one. The desktop app adds automations (cron or heartbeat) and tools
for coordinating user-visible threads. The harness also runs its own internals (auto-review, memory
consolidation) as sub-agents.

## Diagram
```
  user input  (start turn / steer / queued follow-up)
        |
        v
  +----------- /root  (root role) ------------------------------------------+
  | spawn_agent  -> new child; context inherited per fork_turns (none|all|N) |
  | followup_task -> wakes an idle child or queues for a busy one            |
  | send_message  -> drops into the child's mailbox; no turn is started      |
  | wait_agent    -> returns on any mailbox activity or a user steer         |
  | interrupt_agent / list_agents (by path prefix) / close_agent (older set) |
  +---------------------------+---------------------------------------------+
                              |
              /root/<task>  --spawns-->  /root/<task>/<subtask>   (depth cap)
                              |
         child's final reply goes back up as a FINAL_ANSWER mailbox message;
         every agent's rollout records the messages it sent and received

  Shared board (optional, SQLite)  : channels, posts, subscriptions;
                                     only agents mid-turn get notified
  Long-running commands            : PTY session id, polled through write_stdin;
                                     awaiter role; app-server can list/kill
  Outside the local process        : cloud tasks (best-of-N, applied as a patch)
  Desktop automations              : cron -> fresh thread (maybe a worktree)
                                     heartbeat -> structured wake-up of a thread
```

## Mechanics

### Two generations of collaboration tools
| Tool | Generation | Behaviour |
|---|---|---|
| `spawn_agent` | both | Starts a child. The older version takes a message or items, a role, a full-history fork boolean, and model/effort overrides; leaving out the role inherits the parent's role with a full fork. The newer version adds `task_name` and `fork_turns`. |
| `send_input` | older | Sends more input to a child, with an option to interrupt and redirect at once. |
| `wait_agent` | both | Older: waits on a list of ids, returns whichever finishes first, returns empty on timeout. Newer: blocks until some live agent posts to the mailbox or the user steers, and gives back a status summary rather than the content. |
| `close_agent` / `resume_agent` | older | Finished agents still count toward the concurrency limit until they are closed. Closed agents can be reopened. |
| `send_message` | newer | Delivered promptly to the mailbox and does not start a turn. |
| `followup_task` | newer | Starts a turn if the target is idle. If the target is running, the task is delivered at the next message boundary or once the pending tool call finishes. |
| `interrupt_agent` | newer | Interrupts a live agent. |
| `list_agents` | newer | Lists live agents in the current root tree, filtered by path prefix. |

- In the newer generation, task names resolve relative to the caller's path. A child named `task_3`
  spawned from `/root/task1` becomes `/root/task1/task_3`. `fork_turns` accepts `none`, `all`, or a
  positive integer given as a string. The older boolean fork is rejected.
- Wait timeouts are clamped to configured minimum, maximum and default values.
- Agent statuses run from pending initialisation through running and completed states to interrupted
  and not-found.
- Messages sent into an agent's context carry a type (new task, message, final answer, channel post),
  a task name, a sender and a payload. Results from sub-agents therefore appear to arrive as synthetic
  inbound messages. The parent keeps working and receives them at message boundaries, never in the
  middle of a tool call.
- Inter-agent messages are persisted in rollouts. The DB stores each agent's nickname, role and path,
  plus parent/child spawn edges with a status. `SubagentStart` and `SubagentStop` hooks are available.
- The model catalog sets which generation each model uses (the newer one on most models) and a
  reasoning effort for multi-agent work.

### Spawning policy (prompt level)
- The `spawn_agent` description sets a strict bar. The model should spawn only when the user, an
  applicable `AGENTS.md`, or a skill explicitly asks for delegation or parallel agents. A request for
  depth or thoroughness does not count as that permission.
- The rest of the guidance covers keeping the critical path local and handing off sidecar work, giving
  each delegated task its own set of files to write, calling wait rarely, and not redoing delegated work
  itself.
- Role guidance is presented only as a way to choose which agent to use once spawning is allowed. It
  never grants permission to spawn.
- Model overrides are discouraged unless the user asks. Settings control whether the override and
  metadata fields are visible at all.
- The root role prompt describes a team of equally capable peers that share one toolset, and reminds
  the model that a human may read inter-agent messages, so they should be clear.

### Agent roles
| Role | Purpose (paraphrased) |
|---|---|
| default | The general-purpose agent. |
| explorer | Fast, reliable answers to narrow codebase questions. Run several in parallel, trust what they find, and reuse them. |
| worker | Implementation work. The parent assigns explicit file ownership and warns workers that others are editing the same repo, so they must not revert other agents' changes. |
| awaiter | Low reasoning effort and a one-hour (3,600,000 ms) background-terminal timeout. Waits for one command or task, reports only when it finishes, and backs off exponentially between checks. |

- Users can define their own roles with a description, a config file and a pool of nicknames. The config
  file can override model, effort, developer instructions, features and skills.
- Root and sub-agent identity text is injected through dedicated developer-context fragments.

### Limits and cost controls
- The `[agents]` config table includes `max_threads`, `max_depth`, a default model and effort for
  sub-agents, a maximum job runtime, and an interrupt message. When the depth limit is hit, the model is
  told to solve the task itself instead of spawning.
- The newer generation's settings cover the concurrent-thread cap per session, wait timeouts, usage-hint
  text for root and sub-agents, extra developer instructions for sub-agents, tool namespacing,
  enabling or disabling wait and direct messaging, and whether the message board is kept in memory.
- The TUI offers a high-usage reasoning tier aimed at multi-agent work. It warns that usage can climb
  quickly and suggests keeping the per-session concurrent-thread cap below 8.
- A shared, weighted token budget covers the whole agent tree (see note 05).
- An earlier CSV-driven fan-out jobs feature, with per-item assignment, attempt counts and runtime caps,
  has been removed by a migration. A leftover toggle for it remains.

### Agent message board
- The board is an optional extension for large teams. Its tools create and list channels, list threads,
  search, read threads and posts, subscribe, unsubscribe and post. It is stored in SQLite as channels,
  posts, subscriptions, opt-outs and deleted boards.
- Posting subscribes the author automatically. A post can also notify named agents once, without
  subscribing them and without waking idle agents. Only agents with a running turn receive
  notifications, and missed notifications are not stored. A request id makes posts idempotent.
- It appears to work as a blackboard so that large teams avoid a full mesh of direct messages.

### Background terminals
- `exec_command` starts a command inside a PTY. The result is either the output or, if the command is
  still going, a session id to interact with later. `write_stdin` with an empty payload polls. Non-empty writes wait 250 ms by default, up to
  30,000 ms. Empty polls wait between 5,000 and 300,000 ms. `max_output_tokens` defaults to 10,000.
- The number of open processes is capped, and the model is warned when it reaches the cap. The default
  background-terminal timeout is 300,000 ms, and the awaiter role raises it to 3,600,000 ms.
- After an intentional interrupt, the next turn includes a note that background processes may still be
  running.
- The app-server can list, clean and terminate a thread's background terminals. Idle threads appear to
  be unloaded from memory after a configurable delay.

### Persistent mode
A catalog prompt for a persistent mode tells the agent to look for and carry out useful follow-up work
on its own. Because a final answer ends the turn, the prompt has the agent send answers to the user
asynchronously while there is still work to do. The mode is listed next to the highest reasoning tiers
and appears to be meant for newer models.

### Cloud tasks
- `codex cloud` (experimental) lets the user browse tasks, choose an environment, set how many attempts
  to run (best-of-N), choose a branch, page lists of 1–20 with a cursor, and get JSON output. A finished
  task can be applied to the local working tree as a `git apply`.
- The desktop app polls the current task list (limit 20) with a 60-second staleness window.

### Automations (desktop app)
- Automations are stored locally. Each has a status, an RRULE schedule (default: once every 24 hours on
  the hour), next and last run times, working directories, model and effort, a target, a project, a kind,
  an execution environment, a notification policy and an auto-archive option. Each run moves through
  in-progress, then pending review, then accepted or archived. It has an inbox title and summary, and
  a read marker.
- **Cron** automations create a fresh thread for each run. That thread may live in a managed worktree,
  which requires a project folder. **Heartbeat** automations wake an existing target thread.
- A heartbeat arrives as a user-role message wrapped in a heartbeat element. It carries the automation id,
  the current time and the user's instructions. The agent must finish with a structured verdict, notify
  or don't-notify, plus an optional message. A don't-notify verdict hides the run. The guidance says
  routine polling should stay quiet by default.
- The scheduler skips a heartbeat and reschedules it, rather than queueing runs, when the thread is
  missing, busy, or in an ineligible UI or collaboration state. On restart, runs still in progress move
  to pending review.
- The model manages automations through an app tool (create, update, list, peek; stopping one means
  disabling it), not raw directives. A heartbeat that is no longer useful should delete itself.

### Cross-thread coordination (desktop app)
- A thread-coordination section provides tools to create, fork, list (active and archived), read, wait
  on, message, hand off, pin, archive and retitle user-visible threads. `wait_threads` accepts 1–8
  targets and a cursor, wakes on the first completion or attention request, and returns a snapshot when
  the timeout is 0.
- The guidance limits creating new threads to explicit user requests and sends subtasks of the current
  request to the multi-agent tools. The split appears to be that sub-agents are temporary and owned by
  the parent turn, while threads are owned by the user, appear in the sidebar, and are coordinated
  through cursor-based waits.

### Harness-internal sub-agents
- The automatic approval reviewer runs as a sub-agent thread, shown to the user as an approval review.
  It can reuse the parent's compacted history.
- The memory consolidation step is itself a spawned sub-agent, which holds global ownership by
  heartbeating.
- A separate "detached" review delivery has been deprecated. Reviews now run as normal threads.

## Harness design takeaways
- **T1** — Address agents by hierarchical path (`/root/a/b`), accepting relative or canonical names.
  Make the tree queryable by prefix and persist parent/child edges with status.
- **T2** — Keep "message" and "task" apart. A message goes to the mailbox and starts no turn. A follow-up
  task wakes the agent or starts a turn. An agent that is already running gets either one between messages or
  when its current tool call returns, never by preemption in the middle of a tool.
- **T3** — Return sub-agent results as inbound messages instead of blocking tool returns. The wait call
  returns only a status summary and wakes on any mailbox update or user steer, and the content arrives
  through the message stream.
- **T4** — Expose context inheritance as a setting (none, all, or the last N turns) rather than a
  boolean, and build forks by pointing at an ordinal in the parent's rollout, which is cheap and
  cache-friendly.
- **T5** — Put the spawning gate in the tool description and state that a request for thoroughness is
  not permission. Role guidance helps choose an agent but never authorizes spawning.
- **T6** — Ship four built-in roles (default, explorer, worker, awaiter) as config overlays for
  model, effort, instructions and features. Let users add roles, with nickname pools to keep the UI
  readable.
- **T7** — Enforce limits at three levels. A depth cap returns a message the model can act on ("do it
  yourself"). A cap on concurrent threads counts finished agents until they are closed. A weighted token
  budget is shared across the whole tree.
- **T8** — For larger teams, add a blackboard with channels, threads and subscriptions. Make posts
  idempotent and send notifications only to agents that are running, without storing missed ones, to
  avoid storms of wake-ups.
- **T9** — Run long commands as PTY sessions that the model polls (an empty write is a long-yield poll).
  Cap the number of processes, tell the model after an interrupt that background processes may still be
  running, and provide a low-effort awaiter role that backs off exponentially.
- **T10** — Give heartbeat automations a structured notify/don't-notify verdict, keep them quiet by
  default, let them delete themselves when obsolete, and have the scheduler skip and reschedule when the
  target thread is busy.
- **T11** — Keep threads that the user owns distinct from sub-agents, in the tool set and in the prompt. Use cursors for
  cross-thread waits so output that was already delivered is not sent again.
- **T12** — Run harness internals such as the safety reviewer and memory consolidation on the same
  sub-agent machinery, so they get persistence, locking and observability without extra work.
- **T13** — Make run states crash-safe. After a restart, any automation run still marked in progress should land
  in pending review instead of being dropped silently or run again.

## Open questions
- What are the default values of `max_threads`, `max_depth`, the per-session concurrent-thread cap, and
  the background process cap? Only the error text is visible, and the numbers appear to be compiled in.
- Do sub-agents run inside the same app-server process or in separate processes? Per-thread writer locks
  suggest there can be several writers, but not where they run.
- How do inbound inter-agent messages appear in the parent's context (as response items or as developer
  messages), and how does compaction handle them?
- Is the message board scoped to each root thread? The schema suggests so, but this is not confirmed.
- How does cloud best-of-N choose the winning attempt, and how do attempts map to a local apply?
- Are the persistent mode and the highest multi-agent tier gated on the server by plan or rate limits?
