#!/usr/bin/env python3
"""Minimal, stdlib-only Electron ASAR extractor.

Usage:
    python3 -I unasar.py <app.asar> <outdir>

Format (as implemented by the public `@electron/asar` package):
    uint32 LE  = 4                  (size of the next field)
    uint32 LE  = headerPickleSize   (size of the header pickle)
    header pickle:
        uint32 LE payloadSize
        uint32 LE jsonLength
        jsonLength bytes of UTF-8 JSON (padded to 4 bytes)
    file data, each file at 8 + headerPickleSize + int(entry["offset"])

JSON shape: {"files": {name: {"files": {...}} | {"offset": "N", "size": N}
                                   | {"unpacked": true, ...} | {"link": "..."}}}

Unpacked entries (stored in <app.asar>.unpacked/) and symlink entries are
skipped and reported. Any path that would escape <outdir> is refused.
"""
import json
import os
import struct
import sys


def read_header(f):
    size_field, header_size = struct.unpack("<II", f.read(8))
    if size_field != 4:
        raise ValueError(f"unexpected first field {size_field}, not an ASAR?")
    pickle = f.read(header_size)
    if len(pickle) < 8:
        raise ValueError("truncated header pickle")
    _payload, json_len = struct.unpack("<II", pickle[:8])
    header = json.loads(pickle[8:8 + json_len].decode("utf-8"))
    return header, 8 + header_size


def safe_join(root, rel):
    if os.path.isabs(rel) or "\x00" in rel:
        raise ValueError(f"unsafe path: {rel!r}")
    dest = os.path.realpath(os.path.join(root, rel))
    if dest != root and not dest.startswith(root + os.sep):
        raise ValueError(f"path escapes outdir: {rel!r}")
    return dest


def walk(node, prefix=""):
    for name, entry in node.get("files", {}).items():
        if name in ("", ".", "..") or "/" in name or "\\" in name:
            yield ("bad", prefix + name, entry)
            continue
        rel = prefix + name
        if "files" in entry:
            yield ("dir", rel, entry)
            yield from walk(entry, rel + "/")
        elif "link" in entry:
            yield ("link", rel, entry)
        elif entry.get("unpacked"):
            yield ("unpacked", rel, entry)
        else:
            yield ("file", rel, entry)


def extract(asar_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    root = os.path.realpath(outdir)
    stats = {"file": 0, "dir": 0, "link": 0, "unpacked": 0, "bad": 0, "bytes": 0}
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        total = os.fstat(f.fileno()).st_size
        for kind, rel, entry in walk(header):
            stats[kind] += 1
            if kind in ("link", "unpacked", "bad"):
                print(f"skip {kind}: {rel}", file=sys.stderr)
                continue
            try:
                dest = safe_join(root, rel)
            except ValueError as e:
                stats["bad"] += 1
                print(f"skip: {e}", file=sys.stderr)
                continue
            if kind == "dir":
                os.makedirs(dest, exist_ok=True)
                continue
            off, size = base + int(entry["offset"]), int(entry["size"])
            if off + size > total:
                print(f"skip truncated: {rel}", file=sys.stderr)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            f.seek(off)
            with open(dest, "wb") as out:
                remaining = size
                while remaining:
                    chunk = f.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            stats["bytes"] += size
    return stats


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    stats = extract(argv[1], argv[2])
    print(" ".join(f"{k}={v}" for k, v in stats.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
