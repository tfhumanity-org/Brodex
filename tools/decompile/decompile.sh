#!/usr/bin/env bash
# decompile.sh — repeatable Ghidra headless decompilation pipeline.
#
# Two phases over a single shared Ghidra project:
#   analyze  import <binary>, run FastAnalysisPre (prescript) + auto-analysis +
#            ExportFunctions (postscript). Produces $OUT/functions.tsv.
#   select   re-open the already-imported program with NO re-analysis, run
#            DecompileSelect (postscript). Produces $OUT/decomp/*.c + index.
#            Selection via CU_TARGETS / CU_NAME_KEYWORDS / CU_STRING_KEYWORDS.
#   all      analyze then select.
#
# Usage:
#   tools/decompile/decompile.sh analyze <binary> [-- script args...]
#   tools/decompile/decompile.sh select  <binary>
#   tools/decompile/decompile.sh all     <binary> [-- script args...]
#
# Key env (all optional unless noted):
#   OUT                output dir        (default: ./.decompile-out)
#   GHIDRA_HEADLESS    analyzeHeadless path (default: autodetected)
#   JAVA_HOME          JDK 21 home       (default: autodetected via brew)
#   PROJECT_DIR        Ghidra project dir(default: $OUT/project)
#   PROJECT_NAME       Ghidra project    (default: decompile)
#   PROCESSOR          -processor spec   (default: AARCH64:LE:64:AppleSilicon)
#   ANALYSIS_TIMEOUT   per-file seconds  (default: 1200)
#   PRE_ARGS           args to FastAnalysisPre, e.g. "noobjc"
#   CU_TARGETS, CU_NAME_KEYWORDS, CU_STRING_KEYWORDS, CU_MAX,
#   CU_DECOMP_TIMEOUT, CU_MAX_LINES  — consumed by DecompileSelect (see its header)
#
# STATIC ONLY: this imports a *copy* of the shipped binary into Ghidra and never
# launches, attaches to, or modifies the target app. Point <binary> at a file
# you are permitted to read (a shipped executable inside an app bundle).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA_SCRIPTS="$SCRIPT_DIR/ghidra"

PHASE="${1:-}"
BINARY="${2:-}"
# collect script args after a literal `--`
PRE_ARGS="${PRE_ARGS:-}"
if [[ "${3:-}" == "--" ]]; then shift 3 || true; PRE_ARGS="$*"; fi

usage() {
  sed -n '2,40p' "$0"
  exit "${1:-1}"
}

case "$PHASE" in
  analyze|select|all) ;;
  -h|--help|help|"") usage 0 ;;
  *) echo "error: unknown phase '$PHASE'" >&2; usage 1 ;;
esac

if [[ -z "$BINARY" ]]; then echo "error: missing <binary>" >&2; usage 1; fi
if [[ ! -f "$BINARY" ]]; then echo "error: binary not found: $BINARY" >&2; exit 1; fi

OUT="${OUT:-$PWD/.decompile-out}"
PROJECT_DIR="${PROJECT_DIR:-$OUT/project}"
PROJECT_NAME="${PROJECT_NAME:-decompile}"
PROCESSOR="${PROCESSOR:-AARCH64:LE:64:AppleSilicon}"
ANALYSIS_TIMEOUT="${ANALYSIS_TIMEOUT:-1200}"
export CU_OUT_DIR="${CU_OUT_DIR:-$OUT}"
mkdir -p "$OUT" "$PROJECT_DIR"

# --- locate JAVA_HOME (JDK 21) ---
if [[ -z "${JAVA_HOME:-}" ]]; then
  for cand in \
    /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
    /usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home; do
    [[ -d "$cand" ]] && { export JAVA_HOME="$cand"; break; }
  done
fi
if command -v /usr/libexec/java_home >/dev/null 2>&1 && [[ -z "${JAVA_HOME:-}" ]]; then
  JAVA_HOME="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"; export JAVA_HOME
fi
[[ -n "${JAVA_HOME:-}" ]] && export PATH="$JAVA_HOME/bin:$PATH"

# --- locate analyzeHeadless ---
HL="${GHIDRA_HEADLESS:-}"
if [[ -z "$HL" ]]; then
  for cand in \
    /opt/homebrew/Cellar/ghidra/*/libexec/support/analyzeHeadless \
    /usr/local/Cellar/ghidra/*/libexec/support/analyzeHeadless \
    "${GHIDRA_INSTALL_DIR:-}/support/analyzeHeadless"; do
    [[ -x "$cand" ]] && { HL="$cand"; break; }
  done
fi
if [[ -z "$HL" || ! -x "$HL" ]]; then
  echo "error: analyzeHeadless not found; set GHIDRA_HEADLESS or GHIDRA_INSTALL_DIR" >&2
  exit 1
fi

PROG_NAME="$(basename "$BINARY")"
echo "pipeline: phase=$PHASE binary=$BINARY out=$OUT"
echo "  ghidra=$HL java=$JAVA_HOME"

run_analyze() {
  echo "== analyze: import + FastAnalysisPre + ExportFunctions =="
  local args=()
  [[ -n "$PRE_ARGS" ]] && args=(-preScript FastAnalysisPre $PRE_ARGS) || args=(-preScript FastAnalysisPre)
  "$HL" "$PROJECT_DIR" "$PROJECT_NAME" \
    -import "$BINARY" \
    -overwrite \
    -processor "$PROCESSOR" \
    -scriptPath "$GHIDRA_SCRIPTS" \
    "${args[@]}" \
    -postScript ExportFunctions \
    -analysisTimeoutPerFile "$ANALYSIS_TIMEOUT" \
    2>&1 | tee "$OUT/analyze.log"
  echo "functions.tsv: $(wc -l < "$OUT/functions.tsv" 2>/dev/null || echo 0) rows (incl header)"
}

run_select() {
  echo "== select: DecompileSelect (no re-analysis) =="
  "$HL" "$PROJECT_DIR" "$PROJECT_NAME" \
    -process "$PROG_NAME" \
    -noanalysis \
    -scriptPath "$GHIDRA_SCRIPTS" \
    -postScript DecompileSelect \
    2>&1 | tee "$OUT/select.log"
  echo "decompiled: $(ls "$OUT/decomp" 2>/dev/null | wc -l | tr -d ' ') .c files"
}

case "$PHASE" in
  analyze) run_analyze ;;
  select)  run_select ;;
  all)     run_analyze; run_select ;;
esac
echo "done."
