# tools/decompile — repeatable Ghidra decompilation pipeline

A committed, scriptable way to analyze a large native Mach-O (the kind of
Swift/ObjC service binary a desktop agent ships) with Ghidra headless, export a
function inventory, and decompile a chosen subset to C — without hand-driving
the GUI each time.

**Static only.** This imports a *copy* of a shipped binary into a Ghidra
project. It never launches, attaches to, or modifies the target app. Point it at
a file you are permitted to read (e.g. the executable inside an app bundle). Do
not point it at user data.

## Why it exists

Default Ghidra auto-analysis on a ~23 MB Swift binary stalls for a very long
time in a few analyzers. `FastAnalysisPre.java` turns those off up front so the
run finishes, and the two postscripts produce the artifacts we actually want.

## Components (`ghidra/`)

Each file's public class name matches its filename — a hard Ghidra requirement
(a mismatch yields `ClassNotFoundException` at script-load time).

- **`FastAnalysisPre.java`** — prescript. Disables the expensive analyzers
  (*Decompiler Parameter ID*, *Decompiler Switch Analysis*, *Call Convention
  ID*, plus Stack / Shared Return Calls / Aggressive Instruction Finder /
  Variadic override). Objective-C message analysis stays **on** by default; pass
  the script arg `noobjc` to disable it too. Unknown option names are logged and
  skipped, so it is safe across Ghidra versions.

- **`ExportFunctions.java`** — postscript. Writes `functions.tsv`, one row per
  function: `entry`, `size`, `name`, `demangled`, `namespace`, `xrefs`
  (references to the entry point), and up to 6 referenced string literals.
  This is the inventory you grep to choose decompilation targets.

- **`DecompileSelect.java`** — postscript. Decompiles a *selected* subset to
  `decomp/<name>__<entry>.c` plus `decomp_index.tsv`. A function is selected if
  it matches **any** of:
  - an entry address listed in `CU_TARGETS` (TSV, col 1 = hex address, optional
    col 2 = label);
  - a case-insensitive substring in `CU_NAME_KEYWORDS` (matched on the
    demangled name);
  - a case-insensitive substring in `CU_STRING_KEYWORDS` (matched on referenced
    string literals).

  With no selection set, it decompiles nothing (by design — pick targets first).

## Driver (`decompile.sh`)

```sh
# 1. Inventory: import + fast analysis + functions.tsv
OUT=./.decompile-out \
  tools/decompile/decompile.sh analyze "/path/to/SomeService"

# (to also skip Objective-C analysis)
OUT=./.decompile-out PRE_ARGS=noobjc \
  tools/decompile/decompile.sh analyze "/path/to/SomeService"

# 2. Pick targets from the inventory
grep -i 'authoriz\|screenshot\|permission' .decompile-out/functions.tsv

# 3. Decompile a selection (no re-analysis; reuses the imported program)
OUT=./.decompile-out \
  CU_NAME_KEYWORDS='authoriz,screenshot,permission' \
  CU_STRING_KEYWORDS='screenIsLocked,forbidden' \
  tools/decompile/decompile.sh select "/path/to/SomeService"

# or do both phases at once
OUT=./.decompile-out CU_NAME_KEYWORDS='authoriz' \
  tools/decompile/decompile.sh all "/path/to/SomeService"
```

The driver autodetects `analyzeHeadless` (Homebrew `ghidra`, or
`GHIDRA_INSTALL_DIR`) and a JDK 21 `JAVA_HOME`; override with
`GHIDRA_HEADLESS` / `JAVA_HOME`. Other knobs: `PROCESSOR` (default
`AARCH64:LE:64:AppleSilicon`), `ANALYSIS_TIMEOUT`, `PROJECT_DIR`,
`PROJECT_NAME`, and the `CU_*` selection/limit vars (`CU_MAX`,
`CU_DECOMP_TIMEOUT`, `CU_MAX_LINES`) documented in each script's header.

## Outputs (under `$OUT`, default `./.decompile-out`)

| File | From | Contents |
|---|---|---|
| `functions.tsv` | ExportFunctions | full function inventory |
| `decomp/*.c` | DecompileSelect | decompiled C per selected function |
| `decomp_index.tsv` | DecompileSelect | file ↔ entry ↔ name ↔ label index |
| `analyze.log`, `select.log` | driver | headless run logs |

`$OUT` holds raw extracted artifacts and should live under `.extracted/` (or
otherwise gitignored) per the repo's "nothing private is committed" rule — do
not commit decompiled output.
