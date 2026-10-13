# 04 — Permissions, sandbox, approvals  (OpenAI ChatGPT/Codex desktop 26.930, CLI 0.160)

## Overview

Codex answers "may this action run?" with three independent mechanisms and a
policy layer that constrains all three. The **sandbox** enforces a permission
profile at the OS level (Seatbelt on macOS, landlock plus seccomp on Linux, a
restricted token on Windows), and a managed local proxy handles network
egress. The **exec policy** is a small Starlark-based rules language that
marks command prefixes and network destinations as allowed, prompting or
forbidden. The **approval policy** (`approval_policy`) decides *when* to ask,
and the reviewer setting (`approvals_reviewer`) decides *who* answers: the
human, or an automatic LLM reviewer. Managed requirements (`requirements.toml`,
MDM, cloud-delivered policy) limit which values each axis may take. The model
takes an active part: every shell call can declare how much sandbox it needs,
give a user-facing reason, and propose a reusable rule.

## Diagram

```mermaid
flowchart TD
  call[Model shell/exec call<br/>cmd + sandbox_permissions + justification<br/>+ optional prefix_rule / additional_permissions] --> split[Split command into segments<br/>pipes, &&, ||, ;, subshells, $()]
  split --> rules{Exec policy rules match?}
  rules -- forbidden --> stop1[Rejected with policy message]
  rules -- allow --> free[Run; may skip sandbox]
  rules -- prompt / no match --> mode{sandbox_permissions}
  mode -- use_default --> box[Run inside OS sandbox<br/>using the active profile]
  mode -- request extra permissions --> extra[Ask for extra paths or network<br/>still sandboxed]
  mode -- require_escalated --> esc[Ask to run unsandboxed]
  extra --> when{approval_policy}
  esc --> when
  when -- never, or granular category off --> stop2[Auto-reject]
  when -- asks --> who{approvals_reviewer}
  who -- human --> card[Approval card: once / for session /<br/>save rule / allow domain / decline / cancel]
  who -- auto_review --> llm[LLM reviewer<br/>read-only tools, JSON verdict]
  card -- save rule --> store[(append prefix_rule or network_rule<br/>to a .rules file)]
  box -- sandbox denial seen --> retry[Model retries with require_escalated]
  box --> proxy[Local network proxy<br/>domain allowlist, optional TLS interception,<br/>credential injection]
  proxy -- unknown domain --> netok[Network approval → saved network rule]
```

## Mechanics

### Modes, profiles and presets

- The older `sandbox_mode` setting still exists. Each of its three values maps
  onto a built-in permission profile: `read-only` to `:read-only`,
  `workspace-write` to `:workspace`, and `danger-full-access` to
  `:danger-full-access`. Users can define further
  named profiles in config under `[permissions.<name>]`.
- The CLI/TUI offers three presets: Read Only, Default (`auto`: read and edit in
  the workspace and run commands; network access or edits elsewhere need
  approval), and Full Access.
- The desktop composer exposes "agent modes" that are simply named tuples over
  the axes:

  | Mode | Who approves | approval_policy | Profile (sandbox_mode) |
  |---|---|---|---|
  | Read only | human | on-request | `:read-only` (read-only) |
  | Ask for approval (default) | human | on-request | `:workspace` (workspace-write) |
  | Granular | human | per-category table: sandbox, rules and skill prompts off; permission requests and MCP elicitations on | `:workspace` (workspace-write) |
  | Approve for me | LLM reviewer | on-request | `:workspace` (workspace-write) |
  | Full access | human (never asked) | never | `:danger-full-access` (danger-full-access) |

  There are also "custom (from config.toml)", server-default and named-profile
  selections.
- A profile is a TOML table of filesystem entries. Each entry names a path, a
  glob, or a special location (minimal platform defaults, project roots, the
  temp dir, `/tmp`) with access `read`, `write` or `deny`, and has options for
  missing paths and glob scan depth. The profile also sets network to
  restricted or enabled. Glob entries may only be used to deny reads.
- When none of a profile's filesystem entries are understood by the installed
  version, the
  harness keeps filesystem access restricted and tells the user to upgrade. It
  does not fall back to something broader.
- Older allowlist-style knobs are deprecated. Profiles plus
  `default_permissions` / allowed-profile lists replace them.
- In workspace-write, the writable roots are the cwd plus `writable_roots`,
  with options to leave out `$TMPDIR` and `/tmp`. Writable roots that go through
  symlinks are rejected unless the user opts in explicitly.

### macOS Seatbelt profile

- The base policy is deny-by-default and modelled on Chromium's renderer
  sandbox. Process exec, fork, signals and process info are allowed only for
  targets inside the same sandbox. On top of that sit a sysctl read allowlist,
  pseudo-terminal access, POSIX semaphores (Python multiprocessing), the shared
  memory used by OpenMP runtimes, and a few preference/directory-service
  lookups.
- Reads are either fully allowed or limited to a "minimal" platform set
  (system libraries and frameworks, `/etc`, standard `/dev` nodes, firmlink
  parents, `/Applications`).
- Writes are parameterized. The policy is generated **per command**, with
  numbered readable and writable root parameters and `require-not` carve-outs
  for excluded subpaths. Protected ancestor directories get a rule that denies
  unlinking or renaming them. This appears to cover version-control and agent
  config directories inside writable roots (likely `.git`, `.codex`, `.agents`),
  so the agent cannot rename its way around a carve-out.
- Deny-read globs compile to regex deny rules for read and write. The prompt
  lists these paths and tells the model not to try escalating to read them.
- Trailing hardening rules block all XPC service lookups and certain fcntl
  commands.
- Network fragments are added only when network is enabled. In proxy mode
  outbound traffic may go only to localhost and to port 53 for DNS, with local
  bind and the system services TLS needs. In full mode inbound and outbound
  traffic are both allowed. Configured Unix socket paths get bind/connect rules,
  and a loudly named option allows all Unix sockets.
- The profile runs via `/usr/bin/sandbox-exec`. If the generated policy is
  missing, the command fails. Windows backends refuse to run a command
  unsandboxed when they cannot enforce the requested filesystem split.
- `codex sandbox macos` is a debug entry point. Its `--log-denials` flag
  collects Seatbelt denials from the system log and prints them when the
  process exits.
- Violations are recorded as structured events. Output heuristics look for the
  usual OS denial messages (permission errors, a read-only filesystem) to
  classify a failure as sandbox-caused. Sandboxed children get marker environment
  variables such as `CODEX_SANDBOX_NETWORK_DISABLED=1`.

### Managed network proxy

- A built-in proxy (HTTP and SOCKS5, optionally with UDP) is driven by a
  `[network]` config table. The table covers mode, allowed domains, Unix
  sockets, local binding, upstream proxy use, TLS interception, hooks and
  "managed domains only".
- Child processes receive the standard proxy variables (`HTTP_PROXY`,
  `HTTPS_PROXY`, `ALL_PROXY`, plus npm, pip, bundler and docker equivalents), so
  common tools route through it.
- A request to a domain outside the allowlist is blocked with a policy message.
  With TLS interception on, the proxy can enforce HTTPS policy: it restricts
  methods (a limited mode appears to permit only GET, HEAD and OPTIONS), blocks
  private or local targets after CONNECT, and applies hook policy. Its CA
  material lives in files with strict permissions and symlink checks.
- A credential broker lets each provider describe which environment keys hold
  secrets, what the secrets look like, and which URL prefixes they may be sent to. Secrets likely get substituted at egress, so the
  sandboxed process only ever holds a placeholder.
- Network approvals have their own decision type. Approving a domain saves a
  `network_rule` into the exec policy, and the live proxy reloads from those
  rules. Approval requests carry protocol (http, https, SOCKS5 TCP/UDP), host,
  and proposed rule amendments.

### Exec policy language (`.rules`)

- Rules are written in a Starlark dialect with three builtins:
  - `prefix_rule(pattern, decision, justification)`. Pattern elements are
    strings or lists of alternatives. Decisions are `allow`, `prompt` and
    `forbidden`.
  - `network_rule(host, protocol, decision, justification)`.
  - `host_executable(name, paths)`, which pins a bare program name to
    absolute paths, so a rule for `git` means the real git.
- Rule files carry inline positive and negative examples that are checked at
  load time. Every positive example must match some rule and no negative
  example may match, so every policy file is self-testing.
- Load-time validation rejects empty patterns, bad tokens, empty
  justifications and missing decisions. At run time, rejections tell the model
  whether the policy forbids the command or requires approval for it.
- `.rules` files are loaded from config directories. Writes take a file lock,
  so approvals append rules atomically. `codex execpolicy check` tests a
  command against the loaded rules. Managed requirements can also ship rules.
  A migration step appears to turn legacy sandbox allowlists into rules.
- The model is told how segments work: a command is split at pipes, `&&`,
  `||`, `;`, subshells and command substitution, and each segment is checked on
  its own. Segments with redirection, substitution, inline env assignment or
  globs are never auto-allowed by a rule. That keeps an approved prefix from
  quietly covering more than intended.

### Command safety classification

- A tree-sitter parser handles Bash, and another handles PowerShell. Separate
  dangerous-command checks exist for POSIX and Windows. The known-safe list
  appears to be compiled into code and could not be recovered from string data.
- Approval requests include a parsed form of the command, so the UI can show
  summaries such as "reads X" or "searches Y" next to the raw text.
- Semantic risk judgement is left to the automatic reviewer (below).

### Escalation protocol (model ↔ harness)

- Shell/exec tool parameters:
  - `sandbox_permissions`: `use_default` (the default),
    a value that requests extra permissions (combined with an `additional_permissions`
    object listing network and filesystem read/write paths), or
    `require_escalated` (run unsandboxed).
  - `justification`: the question the user sees. It is only valid with an
    explicit escalation.
  - `prefix_rule`: a proposed reusable approval prefix, only valid with
    `require_escalated`.
- Bad combinations fail with explanatory errors, for example a justification
  without an explicit mode, or an additional-permissions object that requests
  nothing.
- A separate `request_permissions` tool asks the client for extra filesystem or
  network access and waits. The client can grant a **subset**, scoped to the
  turn or longer.
- An optional mode (likely behind a feature flag) runs commands in a forked
  zsh with an exec wrapper. Every `execve` a script makes goes over a local
  socket to an escalation server in the core, which decides per exec whether
  to run it sandboxed, escalate it or deny it. Policy then reaches
  sub-processes that a top-level parse would miss.
- Protocol events cover exec, patch and permission approval requests,
  elicitations and reviewer assessments. Possible decisions: approved,
  approved with a saved rule, approved for the session, approved with an MCP
  policy amendment, network rule amendment, denied, timed out and aborted. The
  model gets short, explicit messages when the user rejects a patch or command
  or cancels a network approval.

### Automatic reviewer (LLM approver)

- With `approvals_reviewer = auto_review` ("Approve for me" in the app), a
  separate model answers escalation requests in place of the human. It may look
  around the same machine, but only through read-only commands, and it has no
  network. It is told
  never to ask for sandbox escalation itself.
- The prompt opens by framing the task as judging a single planned agent
  action. It then gives a risk taxonomy, a trust model, calibration guidance,
  optional tenant policy sections, and a strict output schema.
- The verdict is strict JSON with a risk level (low, medium, high, critical),
  the degree of user authorization (unknown, low, medium, high), an outcome
  (allow, deny) and a rationale. Low risk may use a short form with only the
  outcome.
- Thresholds: low and medium are allowed. A high-risk verdict passes only
  if the user has granted medium-or-greater authorization and the action's
  scope is tight. Critical
  is always denied. Explicit user re-approval after a denial can lift a high
  verdict but never a critical one.
- Trust model: the only trusted inputs are user and developer messages,
  `AGENTS.md` files and answers to the user-input tool. Tool output, skill text
  and plugin descriptions count as untrusted evidence. A finding of prompt
  injection needs affirmative evidence.
- The prompt calibrates against over-blocking. A retry after a sandbox denial
  is not suspicious by itself, and the same goes for a path outside the
  workspace. Overwriting common variables like `HOME` is called out as risky.
- Organizations can inject tenant policy text through an `[auto_review]`
  config section. A circuit-breaker setting and a too-many-denials turn error
  appear to stop repeated denied attempts.
- A timeout produces a model-facing message: a timeout is not evidence the
  action is unsafe, and one retry is allowed. A denial produces an instruction
  not to reach the same outcome by workarounds or indirect execution.
- An extra section covers browser and computer-use actions. Sign-ins,
  permission grants and sending sensitive data out are treated as high or
  critical risk. The reviewer also judges MCP elicitations. The planned-action
  payload can include the connected account identity.
- In the desktop app, a nudge offers automatic approval of eligible actions
  and notes that it may use more credits. Availability depends on a rollout
  gate and on requirements. Some models require the automatic reviewer, and
  security-focused models show a warning recommending it.

### Granular approval policy

- `approval_policy` can be a table with five boolean categories: sandbox
  approvals, rule prompts, skill approvals, permission requests and MCP
  elicitations. A category set to false is rejected automatically instead of
  prompting. The prompt lists which categories can still prompt and which are
  auto-rejected.

### Approval UI flow (desktop)

- The app server sends JSON-RPC requests to the client for command-execution
  approval, file-change approval, permissions approval, user input and MCP
  elicitation. Older exec/patch approval methods still work. Requests queue per
  conversation and mark the thread unread. When several windows are open, one
  forwards decisions to another.
- On interrupt or teardown, every pending request gets an automatic answer:
  commands and file changes are declined, permission requests get an empty
  grant scoped to the turn, and user-input requests get empty answers. The
  agent loop can never stall on an orphaned prompt.
- Decisions on the wire: accept, accept for session, accept and save an exec
  policy amendment, apply a network policy amendment, decline, cancel. The
  core sends the list of available decisions, so the server decides which
  buttons the UI shows.
- Card copy asks whether to run the command, offers to allow similar commands
  (by prefix) for the conversation, asks before connecting to a destination,
  and offers to allow all further edits in the conversation.
- Switching to full access opens a confirmation dialog listing the
  consequences (any file anywhere, installing software and changing system
  settings, any website and enabled plugins). The option can be disabled by
  `requirements.toml`, by enterprise policy, in cloud mode, or by a workspace
  that requires sandboxed defaults.

### Trusted projects and hook trust

- `[projects."<path>"] trust_level = "trusted" | "untrusted"` in `config.toml`
  is written during onboarding. The desktop app asks whether to trust a folder
  and offers to open an untrusted one in a restricted state, where project code
  does not run automatically. An untrusted project likely also ignores
  project-supplied config, hooks and rules.
- Hooks are trusted by content hash. The hook-trust bypass flag is separate
  from `--dangerously-bypass-approvals-and-sandbox`. A permission-request hook
  event exists next to pre- and post-tool-use events, so external code can
  answer approvals.

### Managed requirements

- Requirements can limit, per axis, which values are permitted: sandbox modes
  (`allowed_sandbox_modes`), approval policies (`allowed_approval_policies`),
  permission profiles and reviewers
  (`allowed_approvals_reviewers`). They can also force managed-only hooks, gate
  browser and computer use, ship rules, and constrain networking.
- Requirements are merged from several layers, including the project, device
  management (MDM), command-line session flags, cloud-delivered requirements and
  config, and older managed-config files. Validation enforces consistency,
  for example a default profile must be set unless both `:workspace` and
  `:read-only` are allowed.
- Packaged desktop clients always inject the permissions section into the
  model prompt.

### Permissions section of the model prompt (described)

- One paragraph per active `sandbox_mode` says what can be read, where edits
  are allowed (cwd plus writable roots) and whether network is available,
  using a templated network-status value.
- One paragraph per `approval_policy` says when the harness will stop for user
  approval. The untrusted mode, for instance, asks for approval unless a rule
  explicitly allows the command.
- Escalation guidance:
  - Put a short yes/no question in `justification`.
  - If a command fails because of the sandbox or a likely sandbox-related
    network error, rerun it with escalation, without messaging the user first.
  - Escalate before destructive actions the user did not ask for (examples
    include removing files or hard git resets).
  - Do not route around approvals through other tools.
  - Prefer sandboxed additional permissions to fully unsandboxed runs.
- Prefix-rule guidance: never propose prefixes that would allow arbitrary
  scripting (bare interpreters, for example) and never propose one for
  destructive commands.
- Dynamic sections list the already-approved command prefixes, the denied-read
  paths, and the granular categories' prompt/reject status.

## Harness design takeaways

- **T1** — Treat the OS sandbox, the rules engine, and the "who answers"
  choice as three orthogonal settings, and define each UI mode as a named
  combination of them. Enterprise policy can then constrain each axis on its own,
  and new modes cost only configuration.
- **T2** — Escalation should be a structured field on the tool call rather
  than something the model writes in prose. Use a three-way sandbox mode, a justification that is literally the user-facing
  question, and an optional proposed persistent rule. Reject inconsistent
  combinations with errors that teach the model the right usage.
- **T3** — Favour narrow, incremental grants over leaving the sandbox entirely.
  The model can request particular paths or network access and remain
  confined, and let the client
  grant a subset scoped to the turn or session.
- **T4** — Give the policy language built-in tests. Inline match/no-match
  examples are checked at load time. Pin executable names to absolute paths so
  a rule for a tool means the real tool.
- **T5** — Evaluate rules per shell segment and fail closed on shell
  complexity. Redirection, substitution, env assignment and globs never get
  rule-based auto-allow, and the model is told so.
- **T6** — Turn an approval into a durable rule in one click. Approving a prefix
  writes a rule into a lock-protected rules file; approving a domain writes a
  network rule. List the approved prefixes in later prompts, which stops
  repeat requests.
- **T7** — Build a fresh OS sandbox profile for each command, starting from
  "deny everything" and filling in parameterized roots and carve-outs. Deny
  unlink and rename on protected ancestor directories so the agent cannot
  escape a carve-out, and refuse writable roots that resolve through symlinks.
- **T8** — Send all network traffic through a local proxy, and let the sandbox
  reach only localhost and DNS. The proxy enforces domain allowlists, method
  limits through TLS interception, private-address blocking, and credential
  injection at egress, so secrets never enter the sandbox.
- **T9** — Intercept process execution inside the shell, not just the
  top-level command string. Policy then applies to every sub-process a script
  starts.
- **T10** — Structure the LLM approver: a strict JSON risk × authorization
  verdict with fixed thresholds, read-only and offline inspection tools, an
  explicit list of trusted evidence, a timeout message that tells the agent a
  timeout is not a denial, and, once something is denied, a rule against
  reaching the same goal by another route.
- **T11** — Tune the reviewer so it does not refuse too much, by naming
  common false positives: sandbox retries, out-of-workspace paths, small
  verified deletions, safe force-pushes to your own branch. A reviewer that
  refuses too often makes the agent useless on its own.
- **T12** — When a turn is interrupted, resolve each outstanding request
  automatically (decline or empty grant) so the loop cannot hang. Let the server send the valid decisions
  so the UI shows only legal buttons.
- **T13** — Treat trust as data: per-project trust levels and per-hook content
  hashes. Open untrusted folders in a restricted state instead of refusing.
  Each bypass gets its own flag, with "dangerously" in the name.
- **T14** — Fail closed when the platform cannot enforce the requested policy,
  for example by refusing to run unsandboxed or keeping filesystem access
  restricted for unknown profile formats. Never degrade silently.
- **T15** — Make sandbox denials observable through structured violation
  events, an OS-log denial capture mode and output heuristics. The model then
  gets a reliable "failed because of the sandbox, so escalate" signal.

## Open questions

1. What exactly is on the known-safe command list and the dangerous-pattern
   list? They seem to be compiled into code and could not be read statically.
2. What does the legacy `on-failure` approval policy do now? It still parses,
   but no current prompt text for it was found.
3. Which directory names are protected from unlink/rename, and how are they
   resolved for git worktrees?
4. What are the semantics of the network modes (limited vs full), and when is
   TLS interception mandatory?
5. What values does the reviewer's circuit-breaker setting take, and how many
   denials end a turn?
6. What values can the network-status template variable in the prompt take,
   and how does it read in proxy-limited mode?
7. How exactly does an untrusted project change the effective defaults (forced
   read-only? ignored project rules and hooks)?
8. What exactly does the legacy-allowlist-to-rules migration convert?
