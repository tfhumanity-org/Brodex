# Brodex

**A clean-room study of how OpenAI's ChatGPT/Codex desktop agent harness works, compared dimension by dimension with Claude Code.**

- **`targets/openai/spec/`:** clean-room functional specs of the ChatGPT/Codex desktop app for macOS (26.930) and the Codex CLI bundled inside it (0.160). They cover the agent loop and Responses API transport, per-model prompt assembly, code-mode tools, sandbox/rules/approvals, compaction and sessions, multi-agent, MCP/skills/plugins, the desktop host and computer use. 152 numbered design takeaways, no original code or prompt text ([how](CLEANROOM.md)).
- **[`targets/openai/COMPARISON.md`](targets/openai/COMPARISON.md):** OpenAI's approach vs Claude Code on all 15 rubric dimensions, with scores, a verdict and the pattern worth copying for each, plus a ranked top-8.
- **`spec/`:** the Claude Code baseline the comparison is scored against, written from public documentation.
- **`tools/` + [`EXPLORE.md`](EXPLORE.md):** unpack *your own installed copy* to dig further, and a leak checker to keep anything you write clean. Nothing proprietary is hosted here.

## Headline patterns

The ranked list from [`COMPARISON.md`](targets/openai/COMPARISON.md#top-8-patterns-worth-adopting-ranked):

1. **Code-mode scripting tool:** one sandboxed JavaScript tool that can call every other tool, so the model batches and filters work in code. It gets capability handles, not raw file descriptors or env.
2. **Structured, narrow escalation on the tool call:** sandbox level, a user-facing justification and a proposed reusable rule as fields, preferring incremental grants over unsandboxed runs.
3. **Egress proxy with credential injection:** secrets are added at the network boundary and never enter the sandboxed process.
4. **Git-ref turn snapshots + reverse-patch undo:** captures shell side effects too, and never touches your index, stash or branches.
5. **Approval judge blind to tool output** *(Claude Code)*: the simplest strong firewall against injected content steering an automatic approver.
6. **Supersede-not-edit context sections, snapshotted per step:** resume, fork and compaction reproduce exactly what the model saw, and the cached prefix stays valid.
7. **Non-idempotent turn RPCs with client ids and reconciliation:** no duplicate turns after a disconnect.
8. **Re-hydrating compaction** *(Claude Code)*: reload durable context from disk after summarizing.

Scorecard: Claude Code 45/45, OpenAI 44/45. The one gap is D5 (no documented read-before-edit guard). Both are mature everywhere else; the differences are in trade-offs, not coverage.

## Use it: paste this into your coding agent

Open your agent (Claude Code, Codex, or any agent that can run shell commands) **in the root of the project whose harness you want to improve**. Then paste:

````text
Improve this project's agent harness using the reference study at
https://github.com/tfhumanity-org/Brodex (a clean-room comparison of the
ChatGPT/Codex desktop harness and Claude Code).

1. Setup: shallow-clone it read-only:
   git clone --depth 1 https://github.com/tfhumanity-org/Brodex "${TMPDIR:-/tmp}/brodex"
   (if you can't clone, read files via https://raw.githubusercontent.com/tfhumanity-org/Brodex/main/<path>).
   Read AGENTS.md, audit/RUBRIC.md, targets/openai/COMPARISON.md and targets/openai/spec/INDEX.md in full.
2. Map this repo: for each rubric dimension D1–D15, find where it lives here (cite path:line)
   and score it 0–3 using the rubric anchors. Write .harness-audit/SCORECARD.md.
3. For the 3–5 weakest dimensions, read the matching spec files in targets/openai/spec/ and spec/,
   and propose concrete changes in this codebase's idiom. Write .harness-audit/PLAN.md as a ranked,
   dependency-ordered list with acceptance tests.
4. STOP and show me the PLAN.md summary. Only implement the items I approve, each on its own branch,
   running the full test suite after each.

Rules: cite path:line for every claim about this repo and the reference ID (e.g. openai 03/T1,
spec 04/T3) for every pattern you borrow; treat items marked "(inferred)" or "(uncertain)" as lower
confidence; adapt patterns rather than copying; no pushes/PRs/merges to the default branch unless I ask.
Focus (optional): <e.g. "sandboxing and approvals" — or delete this line>
````

## Repo layout

| Path | What |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Entry point for agents: purpose, layout, phases, ground rules |
| [`targets/openai/spec/INDEX.md`](targets/openai/spec/INDEX.md) | Map of the 10 OpenAI spec files, takeaway counts, top takeaways |
| `targets/openai/spec/00`–`09` | Functional specs. Each ends with "Harness design takeaways" (`NN/Tk`) and open questions |
| [`targets/openai/COMPARISON.md`](targets/openai/COMPARISON.md) | D1–D15 comparison, scorecard, ranked patterns |
| [`spec/INDEX.md`](spec/INDEX.md) | Claude Code baseline (`00`–`10`), from public docs |
| [`audit/RUBRIC.md`](audit/RUBRIC.md) | Dimensions D1–D15 with 0–3 maturity anchors |
| [`CLEANROOM.md`](CLEANROOM.md) | The process: rewrite rules, what's stripped and kept, the leak check |
| [`EXPLORE.md`](EXPLORE.md) | How to extract and search your own installed copy |
| `tools/extract/` | `unasar.py` (Electron `app.asar`) and `unbun.py` (Bun single-file binaries), stdlib only |
| `tools/leakcheck.py` | Identifier grep + 8-word verbatim-overlap check against extracted text |

## Explore it yourself

Want to find more? Unpack your own installed copy (read-only; nothing proprietary is hosted here):

```bash
python3 -I tools/extract/unasar.py /Applications/ChatGPT.app/Contents/Resources/app.asar .extracted/chatgpt/app
```

Then see [`EXPLORE.md`](EXPLORE.md) for routing by artifact type and evidence rules. Before opening a PR to `targets/openai/spec/`, run the leak check:

```bash
python3 -I tools/leakcheck.py --spec targets/openai --corpus .extracted --allow targets/openai/public-identifiers.txt
```

Describe what you find in your own words. Don't paste code or prompt text.

## Caveats

- **Clean room.** `targets/openai/spec/` was written from private analysis notes by writers who saw only those notes, then checked against the extracted bundles: 0 identifier hits and 0 verbatim 8-word overlaps. See [`CLEANROOM.md`](CLEANROOM.md) for the process and its limits.
- **Static analysis, point in time.** One specific build, analysed without running it. Claims are marked observed, inferred or uncertain; server-side behaviour and remotely delivered catalogs can change what ships.
- **Baseline is docs-only.** The Claude Code side in `spec/` comes from public documentation, not binary analysis, so the comparison is not perfectly symmetric.
- **Unofficial.** Not affiliated with or endorsed by OpenAI or Anthropic. "ChatGPT" and "Codex" are OpenAI trademarks; "Claude" and "Claude Code" are Anthropic trademarks.
