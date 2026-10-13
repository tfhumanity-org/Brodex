# EXPLORE.md — investigating a target (static only)

Read `AGENTS.md` hard rules first: never launch/attach to the target, never
read user data. Work only on the shipped bundle, copied into
`.extracted/<vendor>-<version>/`.

## 1. Identify

- App bundle: `Info.plist` (`CFBundleIdentifier`, `CFBundleShortVersionString`),
  `Contents/Resources/`, `Contents/Frameworks/`, `Contents/MacOS/`.
- Packaging signals:
  - `Resources/app.asar` (+ `app.asar.unpacked/`) → Electron
  - large executable containing the `---- Bun! ----` trailer → Bun single-file
  - `node_modules/` or `.js` loose in Resources → plain Node/Electron
  - Swift/ObjC frameworks, no JS → native; Rust often shows `rust_panic`,
    `.cargo/registry` strings
  - Mixed apps are common: native shell + bundled CLI agent binary.
- Record findings in `.private/<vendor>/00-identify.md`.

## 2. Route by artifact type

| Artifact | Extract | Analyze |
|---|---|---|
| Electron ASAR | `python3 -I tools/extract/unasar.py app.asar .extracted/<v>/asar` | REA `analyze_javascript_application` — **summary first**, then drill into modules for the topic at hand; `trace_javascript_semantics`, `recover_javascript_sources` when sourcemaps exist |
| Bun single-file | `python3 -I tools/extract/unbun.py <bin> .extracted/<v>/bun` | Same JS route on the extracted modules; if only fallback regions came out, grep them and note lower confidence |
| Native Swift/ObjC/Rust | none (open in place, read-only) | REA `open_binary` → `inspect_macho` → `analyze_swift_types` / `get_objc_classes` → `search_strings` → `procedure_pseudo_code` on relevant procedures → `close_binary` |
| Anything | `strings -n 8 <bin> > .extracted/<v>/strings/<bin>.txt` | grep for tool names, settings keys, prompt-like prose, URL schemes, IPC channel names |
| Plists / resources | copy | REA `inspect_plist`, `inspect_asset_catalog`, `decode_interface_builder` |

Tips:
- Start from user-visible surface (tool names, settings keys, slash
  commands, CLI flags) and trace inward; it keeps notes anchored to public names.
- Prompts in bundles are usually template literals or JSON; find them by
  grepping for distinctive second-person prose ("You are", "Never", "IMPORTANT").
- Prefer REA summaries over dumping whole bundles into context.
- Always run Python against extracted data with `-I`, from outside the
  extraction directory.

## 3. Evidence tagging

Every non-trivial claim in a private note carries:
- **observed** — directly seen in an artifact; cite `path@offset`,
  `file.js:line`, procedure address, or an REA Evidence ID.
- **inferred** — reasoned from observed facts; say from what.
- **uncertain** — plausible, not verified; goes to Open questions.

Citations stay in `.private/`; they are stripped during clean-room rewrite.

## 4. Private note template (`.private/<vendor>/NN-topic.md`)

```markdown
# NN — <Topic> (<vendor> <version>)  [PRIVATE]

## Overview + diagram
Short summary; ascii/mermaid of the components and data flow.

## Mechanics
- Claim. (observed: bundle/main.js@0x1234 | REA ev-…)
- Claim. (inferred from …)

## Key prompt excerpts  (short, private only)
- Purpose of prompt; <= 2-3 short excerpts that justify the description.
  Never copied to public spec.

## Takeaways
- T1 — design lesson …
- T2 — …

## Open questions
- …
```

Also keep `.private/<vendor>/deny.txt`: one regex per line for codenames,
flags, telemetry events, internal hosts found while investigating. It feeds
`tools/leakcheck.py --deny`.

## 5. Hand-off to clean-room

When a topic's notes are complete, rewrite into
`targets/<vendor>/spec/NN-topic.md` per `CLEANROOM.md`, keep T-numbers, and run
the leak check to 0 hits.
