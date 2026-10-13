#!/usr/bin/env python3
"""Best-effort, stdlib-only extractor for Bun single-file executables.

Usage:
    python3 -I unbun.py <executable> <outdir> [--min-region BYTES]

How it works
------------
`bun build --compile` appends a "standalone module graph" to the runtime
binary (on macOS it lives in a dedicated Mach-O section; on Linux/Windows it
is appended to the file). The graph ends with the ASCII trailer
"\\n---- Bun! ----\\n". Immediately before the trailer sits an `Offsets`
struct whose first field is a little-endian u64 `byte_count` (size of the
data blob that precedes the struct) followed by a StringPointer
(u32 offset, u32 length) to the module table. Each module record starts with
StringPointers for name and contents (and, in newer versions, sourcemap and
bytecode), followed by a few u8 enum fields. All offsets are relative to the
start of the blob.

Strategy:
  1. Find the last trailer. For several plausible Offsets struct sizes, derive
     the blob start and validate the module table pointer.
  2. For several plausible module record sizes, accept the first size where
     every record's name/contents pointers are in bounds and names look like
     paths. Write each module's contents to <outdir>/modules/<name>.
  3. If (1) or (2) fails, fall back to dumping "JS-looking" regions: long runs
     of mostly printable UTF-8 that contain JS keywords or the "// @bun"
     banner, written to <outdir>/regions/region_<offset>.js.

Limitations
-----------
* The binary layout is internal to Bun and changes between releases; struct
  sizes are guessed, not parsed from a version field. Validate output.
* Modules compiled to bytecode (`--bytecode`) have no recoverable source;
  only the bytecode blob is written (with a .jsc suffix) when detected.
* Sourcemaps, embedded assets and native addons are written only if they
  appear as module records; their names may be virtual (e.g. /$bunfs/...).
* The fallback region dumper is heuristic: it may split one module into
  several regions or merge adjacent ones, and may miss minified code with
  long non-ASCII string tables.
* Never executes the target. Reads the whole file into memory.
"""
import os
import re
import struct
import sys

TRAILER = b"\n---- Bun! ----\n"
OFFSETS_SIZES = (32, 24, 40, 48, 20)
RECORD_SIZES = (52, 36, 28, 32, 40, 44, 48, 56, 64, 24, 20)
JS_HINT = re.compile(rb"(// @bun|\bfunction\b|\bconst\b|\brequire\(|\bexport\b|=>)")


def sp(buf, off):
    return struct.unpack_from("<II", buf, off)


def looks_like_name(b):
    if not b or len(b) > 1024:
        return False
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return s.isprintable() and ("/" in s or "\\" in s or "." in s)


def parse_graph(data):
    pos = data.rfind(TRAILER)
    if pos < 0:
        return None, "trailer not found"
    for osz in OFFSETS_SIZES:
        start = pos - osz
        if start < 0:
            continue
        byte_count = struct.unpack_from("<Q", data, start)[0]
        blob_start = start - byte_count
        if byte_count == 0 or blob_start < 0:
            continue
        mod_off, mod_len = sp(data, start + 8)
        if mod_len == 0 or mod_off + mod_len > byte_count:
            continue
        blob = memoryview(data)[blob_start:start]
        for rsz in RECORD_SIZES:
            if mod_len % rsz:
                continue
            mods = []
            ok = True
            for i in range(mod_len // rsz):
                r = mod_off + i * rsz
                n_off, n_len = sp(blob, r)
                c_off, c_len = sp(blob, r + 8)
                if n_off + n_len > byte_count or c_off + c_len > byte_count:
                    ok = False
                    break
                name = bytes(blob[n_off:n_off + n_len])
                if not looks_like_name(name):
                    ok = False
                    break
                extra = []
                for k in range(16, rsz - 7, 8):
                    e_off, e_len = sp(blob, r + k)
                    if e_len and e_off + e_len <= byte_count:
                        extra.append((k, bytes(blob[e_off:e_off + e_len])))
                mods.append((name.decode(), bytes(blob[c_off:c_off + c_len]), extra))
            if ok and mods:
                return mods, f"offsets_size={osz} record_size={rsz} modules={len(mods)}"
    return None, "trailer found but module table did not validate"


def safe_dest(root, name):
    rel = re.sub(r"^([A-Za-z]:)?[\\/]+", "", name.replace("\\", "/"))
    rel = "/".join(p for p in rel.split("/") if p not in ("", ".", ".."))
    dest = os.path.realpath(os.path.join(root, rel or "unnamed"))
    if not dest.startswith(root + os.sep):
        raise ValueError(f"path escapes outdir: {name!r}")
    return dest


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def dump_regions(data, root, min_region):
    out = 0
    # printable ASCII + whitespace + UTF-8 continuation/lead bytes
    for m in re.finditer(rb"[\t\n\r\x20-\x7e\xc2-\xf4\x80-\xbf]{%d,}" % min_region, data):
        chunk = m.group(0)
        if len(JS_HINT.findall(chunk[:65536])) < 5:
            continue
        write(os.path.join(root, "regions", f"region_{m.start():#010x}.js"), chunk)
        out += 1
    return out


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Best-effort Bun single-file extractor")
    ap.add_argument("executable")
    ap.add_argument("outdir")
    ap.add_argument("--min-region", type=int, default=4096,
                    help="minimum size of a fallback JS region (bytes)")
    a = ap.parse_args(argv[1:])
    src, outdir, min_region = a.executable, a.outdir, a.min_region
    with open(src, "rb") as f:
        data = f.read()
    os.makedirs(outdir, exist_ok=True)
    root = os.path.realpath(outdir)
    mods, info = parse_graph(data)
    print(f"graph: {info}")
    if mods:
        for name, content, extra in mods:
            try:
                dest = safe_dest(os.path.join(root, "modules"), name)
            except ValueError as e:
                print(f"skip: {e}", file=sys.stderr)
                continue
            write(dest, content)
            for k, blob in extra:
                suffix = ".map" if blob[:1] == b"{" else ".jsc" if k >= 24 else f".extra{k}"
                write(dest + suffix, blob)
            print(f"module {name} ({len(content)} bytes)")
        return 0
    n = dump_regions(data, root, min_region)
    print(f"fallback: wrote {n} JS-looking regions to {os.path.join(root, 'regions')}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
