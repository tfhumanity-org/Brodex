# 02 — System prompt & context assembly  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

Codex no longer ships one monolithic system prompt file. The CLI embeds a model catalog. Each entry
carries a base instruction template and a set of per-feature instruction snippets covering
approvals, plan mode, persistent work, token budgeting and multi-agent roles. At runtime the core
keeps a collection of named context sections, renders each one as a tagged developer- or user-role
message, and persists a snapshot of them in the session rollout. Between steps the sections are
diffed. A section that changed is re-emitted as a fragment that explicitly supersedes the earlier
one, so the already-sent prefix is never rewritten. On the desktop side the app wraps every user
turn in a markdown envelope of attached context (files, IDE state, PR checks, comments) that
precedes the actual request.

## Diagram

```
  Bundled model catalog (offline default; refreshed from backend, overridable in config)
    per model: base template + feature snippets (approvals, plan, persistent,
               token budget, multi-agent role, reviewer variants)
                         |
                         | select by model slug
                         v
  config.toml / profile --> replacement base file (model_instructions_file), developer_instructions,
                            compact_prompt, personality, include-* toggles
  thread start / resume --> base + developer instructions, personality, collaboration mode
                         |
                         v
  +---------------- Context sections (named, one tag each) -----------------+
  | model, permissions, approved prefixes, collaboration mode, persistent,   |
  | apps, plugins, environments, multi-agent, tools, managed instructions,   |
  | context-window guidance, AGENTS.md, platform, realtime ...               |
  |   -> versioned snapshot persisted in rollout; diffed every step          |
  +--------------------------------+-----------------------------------------+
                                   | render changed sections as new fragments
                                   v
  Responses API input
    instructions : base template (few placeholders)
    developer    : permissions | collaboration_mode | skills | apps | plugins |
                   personality | git attribution | multi-agent | budgets | model switch
    user         : AGENTS.md block (<INSTRUCTIONS>) | <environment_context> ...
    (optional)   : memory summary block + lookup procedure
    user turn    : [desktop envelope: files / IDE / comments / checks ...]
                   + fixed "my request" heading + prompt text
```

## Mechanics

### Model catalog as the prompt source

| Aspect | Behaviour |
|---|---|
| Catalog | A JSON catalog of 11 model entries is embedded in the CLI; several are hidden or unreleased. It appears to be refreshed from the backend and cached, with the embedded copy as the offline default and a config override available. |
| Base prompt | The legacy "base instructions" field is empty for every entry. The real base prompt is a per-model template of roughly 17–22 KB. Templating is minimal: a single connector-id placeholder in the newest family, no other variables. |
| Limits per entry | Default context window 272000 tokens, maximum 872000; tool-output truncation at 10000 tokens; an auto-compaction threshold; a minimum client version gate. |
| Tool mode | Newer models run in a code-execution-only tool mode (tools invoked from a scripted `exec` environment); gpt-5.5 keeps the classic direct tool calls. `apply_patch` is exposed as a freeform tool. |

Three template generations coexist:

- **Newest frontier family.** Introduces Codex as sharing a workspace with the user. Sections cover
  when to seek permission, autonomy/persistence, personality, writing style, technical
  communication, PR descriptions, the split between progress commentary and final answers,
  formatting, visualizations, rules for getting work done, skills, apps (connectors) and plugins.
  Skills/apps/plugins guidance is baked into this template rather than injected separately.
- **Intermediate generation.** Same broad shape, plus dedicated guidance about risky/irreversible
  operations and limits on how files may be edited.
- **gpt-5.5.** An older layout: general guidance, engineering judgement, frontend guidance, special
  user requests, and intermediary updates.

For models whose template does not include it, a separate plugins/skills usage snippet is injected.
The older CLI-era prompt (terminal coding assistant; sections on how to work, AGENTS.md spec,
responsiveness, planning, validation, ambition vs precision, final-answer structure, `update_plan`)
is still embedded, likely as a fallback.

Publicly documented config keys can replace or extend layers: `model_instructions_file`,
`developer_instructions`, `compact_prompt`, `personality`, plus boolean toggles (all on by default)
that suppress the permissions, apps, collaboration-mode and environment-context blocks.

### Context sections and fragment tags

- Each section has a stable ID and a matching tag. Developer-role tags cover permissions, model
  switch, managed (admin) developer instructions, persistent mode, apps, collaboration mode,
  multi-agent role and mode, execution environments, git attribution, plugins, realtime
  conversation, skills, tools, personality, token budget, context window and rollout budget.
- User-role tags cover the environment context, user-run shell commands, aborted turns, image
  resize notices, sub-agent notifications, a user-goal context, and current-time reminders (with an
  explicit "unavailable" variant).
- The same canonical tag list is used to recognise harness-injected messages, so they can be
  hidden in the UI, excluded from memory extraction, and kept distinct from genuine user text.
- Snapshots are written to the rollout alongside turn context, compaction records, replacement
  history and context-window IDs. Duplicate section IDs and failed snapshot restores are guarded.
- **Supersede, don't rewrite.** When AGENTS.md, managed instructions, persistent mode,
  context-window guidance or multi-agent mode changes mid-thread, a new fragment is appended
  stating that it replaces the earlier version, or that the earlier version no longer applies when
  the section is removed. This appears designed to keep the prompt-cache prefix intact.

### AGENTS.md discovery

| Setting | Default |
|---|---|
| `project_doc_max_bytes` | 32768 |
| `project_root_markers` | `.git` |
| `project_doc_fallback_filenames` | empty list |

- Global instructions are read from the Codex home directory, preferring `AGENTS.override.md` over
  `AGENTS.md`.
- Project docs use the same precedence (override file, then `AGENTS.md`, then configured fallback
  names), walking from the project root down to the working directory.
- When the byte budget runs out, the remaining doc is truncated with a logged note. The budget
  likely covers the whole root-to-cwd chain, so deeper files get whatever is left.
- Docs are loaded per execution environment and appear to be refreshed when the cwd, environment
  or files change, then re-emitted through the AGENTS.md section.
- Rendering: a heading naming the directory, with the body wrapped in an `<INSTRUCTIONS>` tag.
- Semantics described in the older prompt: a file governs its folder's subtree, more deeply nested
  files take precedence, and direct system/developer/user instructions outrank any AGENTS.md. Files
  on the root-to-cwd path are already in context and need not be re-read.
- Trust: the prompts list the channels that count as trusted and can establish user authorization
  — user and developer messages, AGENTS.md files, and answers to the `request_user_input` tool.
  The automatic approval reviewer relies on this rule.
- `/init` asks the model to write a contributor-guide style AGENTS.md of a few hundred words
  (200–400) and not to overwrite one that already exists.

### Environment context

- Rendered as structured XML: an `<environment_context>` wrapper containing one or more
  environments, one flagged primary. Fields include cwd, status, shell and shell version, current
  date, timezone, network, filesystem and sub-agents.
- Fields that cannot be determined are emitted with an explicit "unavailable" status rather than
  being omitted.
- With several environments, the prompt explains that each is a separate machine with its own
  files and shell. An environment still starting is not yet usable, and its AGENTS.md, skills and
  MCP tools may appear later. Tools accept an environment ID taken from this block.
- Shell version comes from a probe (including a PowerShell variant). Dates use a UTC timestamp
  format. A separate time-reminder fragment keeps the model's clock current.

### Permissions, sandbox and approvals block

- Generated from the policy enums: one variant per `sandbox_mode` (read-only, workspace-write,
  danger-full-access), with network access and any extra policy substituted in.
- `approval_policy` variants include an untrusted-commands mode and a granular mode. In granular
  mode, categories that are switched off (sandbox approval, rules, permission requests, MCP
  elicitations, skill approval) are auto-rejected instead of prompting.
- Sub-guidance covers when to request escalation; prefix-rule hygiene (overly broad prefixes such
  as a bare interpreter are discouraged, and `rm` or heredocs should never be prefix-approved); a
  preferred order for permission requests (request additional permissions before full
  escalation); the fact that each segment of a compound shell command (pipes, `&&`, `;`,
  substitutions) is evaluated independently; the dynamic list of already-approved prefixes; and
  denied filesystem reads.
- The catalog can swap in a variant saying escalation requests will be checked by an automated
  reviewer against policy.

### Collaboration modes and persistent mode

- The current mode is carried in a `<collaboration_mode>` developer fragment. The prompt states
  that only a new developer fragment can change the mode; user wording and tool descriptions
  cannot. Known modes: Default and Plan. The config enum lists further values (code, execute,
  pair programming, custom) that do not appear to be in use.
- **Plan mode** is conversational and runs in three phases: ground in the environment, clarify
  intent, then discuss implementation. Only non-mutating actions are allowed, and `update_plan`
  errors in this mode. Clarifying questions are asked via the `request_user_input` tool, offering
  between 2 and 4 choices, one marked as recommended. The turn ends with exactly one `<proposed_plan>` block that leaves the
  implementer no open decisions. Tone or imperative phrasing from the user does not switch modes.
- In the newest catalog entries only the Default mode text is overridden. Plan likely falls back
  to the built-in plan prompt.
- The desktop thread state remembers the latest collaboration mode along with its model,
  reasoning effort and developer instructions.
- **Persistent mode** (newer models): the agent stays in a long-running loop until disabled,
  delivering interim answers asynchronously and sleeping between checks. Follow-up work stays
  bounded by the original scope and stopping condition; persistence is not a licence to widen
  the task.
- A highest reasoning-effort tier appears to also enable proactive multi-agent delegation.

### Personality

- Config enum: friendly or pragmatic. The catalog stores a default plus one text per option, and a
  personality developer fragment exists.
- Current templates embed a default persona in their Personality section (a curious, thoughtful
  collaborator in the newest family; a more playful voice in a coding variant). The fragment likely
  overrides this when the user chooses a personality. The desktop sends the personality on thread
  start and resume.
- A separate realtime/voice front-end prompt defines its own playful persona, greets the user by
  first name, and hands all real work to the backend agent without revealing that agent exists.
  Messages are prefixed by speaker role (user vs backend).

### Other developer fragments

- **Model switch:** tells the model the user was previously on a different model and supplies the
  new model's instructions.
- **Git attribution:** either requires a Codex co-author trailer on commits, or states that
  attribution is off for this workspace and earlier attribution instructions should be ignored.
- **Catalog budgets:** when the skills list exceeds its context budget, a notice tells the model
  that host (or cloud) skills exist but were left out of the visible list. Tools have a similar
  budget.
- **Size guard:** additional developer instructions are rejected if they exceed the model's
  context limit (estimated tokens, including markers).

### Context window: compaction and notes

- **Compaction prompt:** asks the model to write a checkpoint handoff summary for a successor
  model, covering progress, constraints, next steps and critical data. On re-injection, a prefix
  explains that another model began the task and summarised its reasoning, and that tool state is
  still available. The UI warns that long threads and repeated compactions reduce accuracy. Remote
  compaction expects exactly one output item.
- **Base-template guidance:** compaction keeps every earlier user request, the newest user message
  is the current steering, and the task continues after compaction rather than restarting.
- **Token-budget alternative** (off by default): instead of compaction, the agent works in explicit
  context windows. A reminder fires when 6144 tokens remain, asking it to save concise progress
  notes and start a new context. At exhaustion, a fallback allows exactly one notes write followed by
  the window switch. Non-assistant history items carry IDs so they can be read back across windows.
  The rollout tracks window numbers and links.

### Review prompt

- Casts the model as reviewing another engineer's change. It lists eight criteria for what counts
  as a bug (introduced by the change, discrete, provable impact, ...) and eight rules for comments.
- Titles carry priority tags P0–P3 plus a numeric priority. Output is a strict JSON object: a
  findings array plus an overall correctness verdict, explanation and confidence score.
- Findings grounded in AGENTS.md must cite the smallest line range of the instruction file that
  supports them.
- Presets: uncommitted changes (staged, unstaged, untracked); against a base branch, with merge base
  substituted; a specific commit, with SHA and title; and custom instructions.

### Memory injection (read path)

- A memory section tells the model when a memory lookup is warranted (skip only for
  self-contained requests), the on-disk layout (summary file, `MEMORY.md`, skills, per-rollout
  summaries), a quick lookup procedure, and an inline memory summary between delimiter lines.
- Citations use a dedicated tag listing entries and rollout IDs. Memory is edited only on an
  explicit remember/forget request, by appending to an ad-hoc notes area.
- The prompt warns that memory does not prove how the code behaves today, and that the real source
  should be checked when it matters. Ad-hoc notes are treated as untrusted.

### Desktop user-message envelope

- The app prepends a markdown preamble to the prompt, followed by a fixed "my request" heading and
  then the user's text. The parser also accepts the older IDE-extension heading, so history
  round-trips.
- Conditional sections, in this order: worktree instructions; response annotations (JSON); a
  suggested artifact action target; an open LaTeX document; IDE context (active file, selection,
  open tabs); files mentioned by the user (label, path, line range, image flag, library/shared
  metadata, uploaded-file pointer); a reminder to keep instructions inside attached documents
  separate from the user's request; pasted files; mentioned applications; prior conversation;
  referenced Codex chats (live references the model must fetch with a thread-reading tool and treat
  as untrusted); a referenced ChatGPT conversation; PR repair instructions; diff and browser
  comments; selected text; MCP app context; failing PR checks; PR merge conflicts; and finally
  in-app browser state (labelled as ambient UI state, not part of the request) or Chrome tabs.
- Thread start/resume carries base and developer instructions, personality, collaboration mode,
  config overrides (e.g. `model_reasoning_effort`) and workspace roots. Automations start threads
  with their own developer instructions and an automation source marker.
- Turn start and steering messages carry a keyed additional-context map. Large values are chunked
  into parts of at most 800 UTF-8 bytes. The CLI exposes a similar hook-supplied additional context
  with a size limit, so hooks and the app likely share this channel.

## Harness design takeaways

- **T1** — Ship prompts as per-model data. One catalog entry holds the base template and every
  feature snippet, which lets prompt changes reach individual models without shipping a new
  client. An embedded copy covers offline use, and entries are gated on a minimum client version.
- **T2** — Model developer context as named sections, each with a stable ID and tag. Diff them per
  step and persist snapshots in the rollout, so resume, fork and compaction reproduce the context the
  model actually received.
- **T3** — Supersede rather than edit. A changed section is followed by a new fragment declaring
  the prior version replaced (or void, if removed). History is never rewritten and
  the cached prefix stays valid.
- **T4** — Keep one canonical registry of injected-message tags. The harness can then strip, hide
  or re-render its own fragments (UI, memory extraction, compaction) without confusing them with
  real user text.
- **T5** — Layer AGENTS.md from the global file down through each directory between root and
  cwd, letting an override file win and allowing extra fallback filenames. Share one byte budget (32 KiB) across the chain, truncate with a
  visible note, re-resolve per environment and refresh on change.
- **T6** — Spell out trust levels inside the prompt itself. List which sources may authorize
  actions (the user, developer messages, AGENTS.md, structured user-input answers), and label ambient UI state and
  referenced chats as untrusted data.
- **T7** — Render environment context as structured XML and mark unknown fields as unavailable
  rather than dropping them. Allow multiple environments, each addressable by ID from tools,
  including ones still booting.
- **T8** — Generate the permissions prompt from the policy enums (sandbox mode, approval policy,
  network) and add actionable guidance: when to escalate, prefix-rule hygiene with bad examples,
  and a reminder that each shell segment is judged separately.
- **T9** — Let only developer messages change the collaboration mode; user imperatives do not. A
  read-only planning mode that finishes with a single parseable plan block lets the UI offer a
  one-click switch to implementation.
- **T10** — Give compaction a concrete contract: a handoff summary for a successor, all user
  messages kept verbatim, the newest one treated as steering. For very long jobs, the
  experimental scheme of saved notes, fresh context windows and ID-addressable history is a good
  model to borrow.
- **T11** — Build the client-side envelope from markdown sections (mentioned files, IDE context,
  selection, PR checks, diff comments) followed by a fixed request heading. Keep it parseable so
  the UI can rebuild attachment chips from history.
- **T12** — Budget catalog-style context (skills, tools). When entries are dropped, tell the model
  so it looks up anything the user names explicitly instead of assuming it is absent.
- **T13** — Give the review prompt a strict JSON output schema with P0–P3 priorities, and make
  any finding based on a repository rule point at the lines of the rule file it relies on.
- **T14** — Provide memory as a compact summary with instructions for digging deeper, rather than
  pasting everything. Add criteria for when to look, a citation format, and a caveat that stored
  memories may be stale relative to the code.
- **T15** — Keep persona separate from capability. A voice/realtime layer owns the persona and hands
  every task to the backend agent through one interface, and each tier has a narrow prompt.

## Open questions

1. In what sequence are developer fragments emitted within a turn? The tag registry order hints at it
   but may not be the actual render order.
2. Does the root-to-cwd instruction chain arrive as a single block with one heading, or as
   separate blocks per directory?
3. What text does the personality fragment carry for friendly vs pragmatic? No standalone text was
   found in this build, so it may come from the remotely refreshed catalog.
4. Is the token-budget (notes / new-context) mode switched on server-side for any model? It is
   disabled in every bundled entry.
5. Do the newest models really fall back to the built-in conversational plan prompt, given that
   their catalog entries leave Plan unset?
6. Do the platform (macOS) and realtime sections carry extra instructions? No text for them was
   found statically.
7. How are additional-context keys other than the app's own chunked key (for example hook-supplied
   ones) rendered for the model?
