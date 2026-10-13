# CLEANROOM.md — private notes → public spec

Public specs (`spec/`, `targets/<vendor>/spec/`) must describe **what a harness
does and why it is designed that way**, in our own words. They must not let a
reader reconstruct the target's code, prompts, or internal naming.

## Rules

1. **Paraphrase only.** Write from understanding, not from the excerpt. A good
   habit: close the private note, then write the paragraph.
2. **No verbatim runs of 8+ words** from any target artifact (code, strings,
   prompts, UI copy, comments). Short public names are fine (see KEEP).
3. **Prompts are described, never quoted.** Cover purpose, structure (sections,
   ordering, conditional parts), and content themes ("tells the model to
   prefer dedicated file tools over shell for reads"). No sentences lifted.
4. **Strip internal identifiers:**
   - minified or internal function/class/variable names
   - file offsets, virtual addresses, line:column positions, `file@offset`
   - telemetry/analytics event names
   - feature-flag, experiment, rollout, and gate names
   - internal codenames, internal URLs/hostnames, staging endpoints
   - build paths (CI runner paths, developer home dirs)
   - unreleased products or features not publicly announced
5. **Keep (allowed) public surface:**
   - tool names as shown to the model/user (e.g. `Read`, `Bash`, `apply_patch`)
   - user-facing settings keys, slash commands, env vars documented publicly
   - documented file formats and paths (e.g. `AGENTS.md`, `~/.claude/settings.json`)
   - CLI flags that appear in public docs or `--help`
   - numeric constants (limits, timeouts, sizes) — stated as numbers
   - public protocol names (MCP, JSON-RPC, SSE, LSP)
6. **When unsure, generalize** ("a gate controls rollout") or move it to
   Open questions without the identifier.
7. **Evidence pointers stay private.** Public specs may say "observed" vs
   "inferred" but never cite offsets or evidence IDs.

## Per-file template

```markdown
# NN — <Topic>  (<vendor> <version or "public docs, YYYY-MM">)

## Overview
3–6 sentences: what this subsystem is for and how it fits the harness.

## Diagram
ascii or mermaid: components, data flow, or state machine.

## Mechanics
Behavioral description, grouped by sub-topic. Use tables for enumerations
(tools, modes, settings). Mark each claim's confidence when not obvious:
(observed) / (inferred) / (uncertain).

## Harness design takeaways
- **T1** — <design lesson> — why it matters / trade-off.
- **T2** — ...
(Numbering is preserved from the private notes so T-ids stay stable across
rewrites; gaps are fine if a takeaway was dropped as non-public.)

## Open questions
- Things not determinable statically, or deliberately left vague.
```

## Leak-check procedure

Run before every commit touching a public spec:

```sh
python3 -I tools/leakcheck.py \
  --spec targets/<vendor>/spec \
  --corpus .extracted/<vendor>-<version> .private/<vendor> \
  --deny .private/<vendor>/deny.txt
```

**(a) Identifier grep** — categories built in:
`hex-offset` (`0x` + 4+ hex digits), `at-address` (`@0x…`), `file@offset`,
`js-line` (`.js:123`), `ci-build-path` (`/Users/runner`, `/home/runner`),
`internal-host` (`*.internal`, `*.corp`, `staging.*`), and
`snake-in-corpus` (3+ part snake_case tokens that also appear in the corpus,
which catches telemetry/flag names). Add a per-vendor `deny.txt` in
`.private/<vendor>/` with one regex per line for codenames, experiment
names, internal product names, and hostnames discovered during investigation.

**(b) Verbatim overlap** — text is lowercased, markdown punctuation stripped,
whitespace collapsed; every 8-word shingle of the spec is hashed, then the
corpus is streamed (text-like files < 20 MB). Any shared shingle is a hit.

**Target: 0 hits.** For each hit, rewrite the sentence (do not just reorder
words), or drop the identifier. If a hit is a genuinely public phrase (e.g. a
documented CLI help string), rephrase anyway — it is cheaper than arguing.
Also do a manual skim for things the tool cannot catch: quoted UI copy shorter
than 8 words that is distinctive, unreleased feature names, and prompt
structure described so precisely it amounts to a transcription.
