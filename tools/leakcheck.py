#!/usr/bin/env python3
"""Clean-room leak check: public spec text vs. private/extracted target text.

Usage:
    python3 -I tools/leakcheck.py --spec targets/<vendor>/spec \\
        --corpus .extracted/<vendor> .private/<vendor> [--deny denylist.txt] \\
        [--examples 5] [--shingle 8]

Reports, per spec file:
  (a) identifier-pattern hits (see PATTERNS below), plus
      - snake_case tokens with 3+ parts (telemetry/flag-looking) that ALSO
        occur in the corpus, and
      - (tokens listed in --allow are exempt: public config keys, API fields,
        model-facing tool names)
      - any regex from --deny (one per line, '#' comments; use for codenames,
        internal hostnames, experiment names)
  (b) verbatim overlap: 8-word shingles (lowercased, markdown punctuation
      stripped, whitespace collapsed) shared between spec and corpus.

Corpus files are streamed; only text-like files < 20 MB are scanned. Spec
shingles are hashed first, then each corpus file is scanned once.
Exit status: 0 = clean, 1 = hits found, 2 = usage error.
"""
import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict

MAX_CORPUS_BYTES = 20 * 1024 * 1024
CHUNK = 1 << 20
SPEC_EXT = {".md", ".markdown", ".txt", ".mmd"}

PATTERNS = {
    "hex-offset": re.compile(r"\b0x[0-9a-fA-F]{4,}\b"),
    "at-address": re.compile(r"@0x[0-9a-fA-F]+"),
    "file@offset": re.compile(r"[\w./-]+\.\w{1,6}@\d+"),
    "js-line": re.compile(r"\.(?:m?js|cjs|ts|tsx|jsx):\d+"),
    "ci-build-path": re.compile(r"/Users/runner\b|/home/runner\b|/builds?/[\w-]+/|C:\\\\a\\\\"),
    "internal-host": re.compile(
        r"\b[\w.-]+\.(?:internal|corp|intranet|lan|local)\b(?!\.[A-Za-z])"  # not CLAUDE.local.md
        r"|\b(?:staging|dev|internal|preprod)[\w-]*\.[\w.-]+\.(?:com|net|io|ai|org)\b"),
}
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+){2,}\b")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
TEXT_EXT_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".woff",
                   ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav", ".zip", ".gz",
                   ".node", ".dylib", ".so", ".dll", ".exe", ".wasm", ".car", ".pdf"}


def words(text):
    return WORD.findall(text.lower())


def h(ws):
    return hashlib.blake2b(" ".join(ws).encode(), digest_size=8).digest()


def iter_files(paths, exts=None):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [d for d in dns if d not in (".git", "node_modules/.cache")]
            for fn in sorted(fns):
                if exts is None or os.path.splitext(fn)[1].lower() in exts:
                    yield os.path.join(dp, fn)


def is_texty(path):
    if os.path.splitext(path)[1].lower() in TEXT_EXT_BINARY:
        return False
    try:
        if os.path.getsize(path) >= MAX_CORPUS_BYTES:
            return False
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    return True


def load_deny(path):
    out = []
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(re.compile(line, re.IGNORECASE))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--corpus", nargs="+", required=True)
    ap.add_argument("--deny")
    ap.add_argument("--allow", help="file of public identifiers (one per line, '#' comments) exempt from snake-in-corpus")
    ap.add_argument("--examples", type=int, default=5)
    ap.add_argument("--shingle", type=int, default=8)
    a = ap.parse_args(argv[1:])
    n = a.shingle
    deny = load_deny(a.deny)
    allow = set()
    if a.allow:
        with open(a.allow, encoding="utf-8") as f:
            allow = {l.split("#")[0].strip() for l in f} - {""}
    spec_root = os.path.realpath(a.spec)

    # --- pass 1: spec ---
    shingles = defaultdict(set)          # hash -> {spec files}
    shingle_text = {}                    # hash -> text (for examples)
    regex_hits = defaultdict(list)       # file -> [(category, line_no, match)]
    snake = defaultdict(set)             # token -> {(file, line_no)}
    spec_files = list(iter_files([a.spec], SPEC_EXT))
    if not spec_files:
        print(f"no spec files under {a.spec}", file=sys.stderr)
        return 2
    for sf in spec_files:
        rel = os.path.relpath(sf, spec_root)
        with open(sf, encoding="utf-8", errors="replace") as f:
            text = f.read()
        for i, line in enumerate(text.splitlines(), 1):
            for cat, rx in PATTERNS.items():
                for m in rx.finditer(line):
                    regex_hits[rel].append((cat, i, m.group(0)))
            for rx in deny:
                for m in rx.finditer(line):
                    regex_hits[rel].append(("deny:" + rx.pattern[:30], i, m.group(0)))
            for m in SNAKE.finditer(line):
                if m.group(0) not in allow:
                    snake[m.group(0)].add((rel, i))
        ws = words(text)
        for j in range(len(ws) - n + 1):
            k = h(ws[j:j + n])
            shingles[k].add(rel)
            shingle_text.setdefault(k, " ".join(ws[j:j + n]))

    # --- pass 2: corpus (streamed) ---
    corpus_root_set = {spec_root}
    matched = {}                          # hash -> corpus file
    snake_seen = set()
    snake_rx = (re.compile(r"\b(?:" + "|".join(map(re.escape, snake)) + r")\b")
                if snake else None)
    scanned = skipped = 0
    for cf in iter_files(a.corpus):
        real = os.path.realpath(cf)
        if any(real == r or real.startswith(r + os.sep) for r in corpus_root_set):
            continue
        if not is_texty(cf):
            skipped += 1
            continue
        scanned += 1
        tail_words, tail_raw = [], ""
        with open(cf, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                s = tail_raw + chunk.decode("utf-8", errors="replace")
                cut = max(s.rfind(" "), s.rfind("\n"))
                if cut < 0 or len(chunk) < CHUNK:
                    cut = len(s)
                body, tail_raw = s[:cut], s[cut:]
                if snake_rx:
                    snake_seen.update(snake_rx.findall(body))
                ws = tail_words + words(body)
                for j in range(len(ws) - n + 1):
                    k = h(ws[j:j + n])
                    if k in shingles and k not in matched:
                        matched[k] = cf
                tail_words = ws[-(n - 1):] if n > 1 else []
            if tail_raw:
                ws = tail_words + words(tail_raw)
                for j in range(len(ws) - n + 1):
                    k = h(ws[j:j + n])
                    if k in shingles and k not in matched:
                        matched[k] = cf
                if snake_rx:
                    snake_seen.update(snake_rx.findall(tail_raw))

    for tok in snake_seen:
        for rel, i in snake[tok]:
            regex_hits[rel].append(("snake-in-corpus", i, tok))

    overlap = defaultdict(list)
    for k, cf in matched.items():
        for rel in shingles[k]:
            overlap[rel].append((shingle_text[k], cf))

    # --- report ---
    print(f"spec files: {len(spec_files)}  corpus files scanned: {scanned}  skipped: {skipped}")
    total = 0
    for sf in spec_files:
        rel = os.path.relpath(sf, spec_root)
        rh, ov = regex_hits.get(rel, []), overlap.get(rel, [])
        total += len(rh) + len(ov)
        status = "OK " if not (rh or ov) else "HIT"
        print(f"{status} {rel}: identifier={len(rh)} shingle{n}={len(ov)}")
        for cat, i, m in sorted(rh, key=lambda x: x[1])[:a.examples]:
            print(f"      [{cat}] line {i}: {m}")
        for txt, cf in ov[:a.examples]:
            print(f"      [verbatim] \"{txt}\"  <- {cf}")
    print(f"TOTAL hits: {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
