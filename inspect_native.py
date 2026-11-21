"""Audit native CAD property fields in memory. The CLI only emits aggregate counts."""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
import re
from pathlib import Path
import secrets
import struct
import uuid
import zlib

import olefile

from pipeline import Identities

NATIVE = {".sldprt", ".sldasm", ".slddrw", ".ipt", ".iam", ".idw"}
CFB = bytes.fromhex("d0cf11e0a1b11ae1")
GENERIC = {"none", "default", "user", "administrator", "autodesk", "inventor", "solidworks", "unknown"}
SUMMARY = {4: "author", 8: "last saved by"}
DOCUMENT = {14: "manager", 15: "company"}
TRACKING = {5: "part number", 7: "project", 41: "designer", 42: "engineer", 43: "authority"}
FIELDS = {
    "f29f85e0-4ff9-1068-ab91-08002b27b3d9": SUMMARY,
    "3d38de39-0588-4c14-bb37-18f4d5dd31c7": SUMMARY,
    "d5cdd502-2e9c-101b-9397-08002b2cf9ae": DOCUMENT,
    "8cf58000-da66-4ae6-8ff0-7b58406fb049": DOCUMENT,
    "32853f0f-3444-11d1-9e93-0060b03c1ca6": TRACKING,
}


def u32(blob, at):
    if at < 0 or at + 4 > len(blob):
        raise ValueError("truncated property table")
    return struct.unpack_from("<I", blob, at)[0]


@dataclass(frozen=True)
class Field:
    section: int
    pid: int
    at: int
    end: int
    wide_type: bool
    codec: str
    value: str

    def encoded(self, alias):
        chars = (alias + "\0").encode(self.codec)
        length = len(chars) // 2 if self.wide_type else len(chars)
        payload = struct.pack("<I", length) + chars
        capacity = self.end - self.at - 4
        if len(payload) > capacity:
            return None
        return payload.ljust(capacity, b"\0")


def fields(blob):
    """Enumerate scalar text only from the specified property sets and IDs."""
    if len(blob) < 48 or blob[:2] != b"\xfe\xff":
        return
    sections = u32(blob, 24)
    if sections not in (1, 2) or len(blob) < 28 + 20 * sections:
        raise ValueError("unrecognized OLEPS header")
    for section in range(sections):
        header = 28 + 20 * section
        known = FIELDS.get(str(uuid.UUID(bytes_le=blob[header:header + 16])))
        if known is None:
            continue
        base = u32(blob, header + 16)
        size, count = u32(blob, base), u32(blob, base + 4)
        end = base + size
        if base < 28 + sections * 20 or end > len(blob) or 8 + 8 * count > size:
            raise ValueError("invalid OLEPS section")
        pairs = [(u32(blob, base + 8 + 8 * i), base + u32(blob, base + 12 + 8 * i)) for i in range(count)]
        offsets = sorted(y for _, y in pairs)
        if (len(set(offsets)) != count or len(dict(pairs)) != count or
                any(y < base + 8 + 8 * count or y + 4 > end for y in offsets)):
            raise ValueError("invalid OLEPS offsets")
        limits = dict(zip(offsets, offsets[1:] + [end]))
        entries = dict(pairs)
        cp = entries.get(1)
        if cp is None or limits[cp] - cp < 6 or u32(blob, cp) != 2:
            raise ValueError("missing OLEPS code page")
        page = struct.unpack_from("<H", blob, cp + 4)[0]
        for pid, at in pairs:
            kind = u32(blob, at)
            if pid not in known or kind not in (30, 31):
                continue
            wide = kind == 31
            encoding = "utf-16le" if wide or page == 1200 else f"cp{page}"
            limit = limits[at]
            if at + 8 > limit:
                raise ValueError("short text field")
            number = u32(blob, at + 4)
            nbytes = number * 2 if wide else number
            if at + 8 + nbytes > limit:
                raise ValueError("invalid property string length")
            try:
                value = blob[at + 8:at + 8 + nbytes].decode(encoding).rstrip("\0")
            except (UnicodeError, LookupError) as error:
                raise ValueError("unsupported property text encoding") from error
            if value and "\0" not in value:
                yield Field(section, pid, at, limit, wide, encoding, value)


def discover(data):
    """Learn typed identities in a private first pass; does not approve native file emission."""
    if not data.startswith(CFB):
        return
    with olefile.OleFileIO(io.BytesIO(data)) as compound:
        for path in compound.listdir():
            stream = compound.openstream(path)
            if stream.read(2) == b"\xfe\xff":
                stream.seek(0)
                for item in fields(stream.read()):
                    yield item.value


def inspect(data, ids):
    counts = Counter(files=1)
    if not data.startswith(CFB):
        counts["non_CFB"] = 1
        return counts
    counts["CFB"] = 1
    original_streams = {}
    patterns = set()
    changes = {}
    memory = io.BytesIO(data)
    with olefile.OleFileIO(memory, write_mode=True) as compound:
        for path in compound.listdir():
            path = tuple(path)
            raw = compound.openstream(list(path)).read()
            if not raw.startswith(b"\xfe\xff"):
                original_streams[path] = hashlib.sha256(raw).digest()
                continue
            counts["OLEPS_candidates"] += 1
            try:
                known = list(fields(raw))
            except ValueError:
                counts["unreadable_property_candidates"] += 1
                continue
            edited = bytearray(raw)
            for item in known:
                counts["typed_identity_fields"] += 1
                ids.learn(item.value)
                alias = ids.alias(item.value)
                if (4 <= len(item.value.strip()) <= 256 and item.value.casefold() not in GENERIC
                        and any(c.isalpha() for c in item.value)):
                    utf8 = re.escape(item.value.encode("utf-8"))
                    wide = re.escape(item.value.encode("utf-16le"))
                    patterns.add(re.compile(rb"(?<![a-zA-Z0-9])" + utf8 + rb"(?![a-zA-Z0-9])"))
                    patterns.add(re.compile(rb"(?<![a-zA-Z0-9]\x00)" + wide + rb"(?![a-zA-Z0-9]\x00)"))
                replacement = item.encoded(alias)
                if replacement is None:
                    counts["full_alias_exceeds_field_capacity"] += 1
                elif item.section == 0:
                    edited[item.at + 4:item.end] = replacement
                    changes[path, item.pid] = (alias, item.codec)
            if edited != raw:
                compound.write_stream(list(path), bytes(edited))
        # Also inspect zlib frames beginning near the start of application streams.
        # Bounded decompression is a partial contents check, never clearance.
        repeated = False
        for path in original_streams:
            raw = compound.openstream(list(path)).read()
            if any(w.search(raw) for w in patterns):
                repeated = True
                continue
            for header in (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda"):
                offset = raw.find(header, 0, 80)
                if offset == -1:
                    continue
                try:
                    decoded = zlib.decompressobj().decompress(raw[offset:], 4 * 1024 * 1024)
                except zlib.error:
                    continue
                if any(w.search(decoded) for w in patterns):
                    repeated = True
        counts["files_with_identity_in_application_stream"] = int(repeated)
    # Reopen independently. olefile's property parser and logical stream reader
    # check field values and every unedited application stream, including geometry.
    with olefile.OleFileIO(io.BytesIO(memory.getvalue())) as reopened:
        for (path, pid), (alias, encoding) in changes.items():
            actual = reopened.getproperties(list(path))[pid]
            if isinstance(actual, bytes):
                if encoding == "utf-16le" and len(actual) % 2:
                    actual += b"\0"
                actual = actual.decode(encoding).rstrip("\0")
            # olefile 0.47 can return bytes after a VT_LPWSTR terminator.
            # Compare the first null-terminated value and verify the actual
            # serialized count independently with the strict section reader.
            strict = next(v.value for v in fields(reopened.openstream(list(path)).read())
                          if v.section == 0 and v.pid == pid)
            if actual.partition("\0")[0] != alias or strict != alias:
                raise ValueError("independent typed property mismatch")
            counts["verified_full_alias_edits_in_memory"] += 1
        for path, sha in original_streams.items():
            if hashlib.sha256(reopened.openstream(list(path)).read()).digest() != sha:
                raise ValueError("application stream changed")
            counts["verified_unchanged_application_streams"] += 1
    counts["files_with_opaque_application_streams"] = int(bool(original_streams))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("private_input", type=Path)
    parser.add_argument("--max-mib", type=int, default=64)
    args = parser.parse_args()
    if not args.private_input.is_dir() or args.max_mib < 1:
        parser.error("supply a private input directory and a positive per-file size limit")
    ids = Identities(secrets.token_bytes(32))
    summary = {ext: Counter() for ext in NATIVE}
    for path in args.private_input.rglob("*"):
        ext = path.suffix.lower()
        if ext not in summary or path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size > args.max_mib * 1024 * 1024:
            summary[ext]["over_limit"] += 1
            continue
        try:
            summary[ext].update(inspect(path.read_bytes(), ids))
        except (ValueError, OSError, KeyError, struct.error):
            summary[ext]["inspection_failed"] += 1
    print(json.dumps({ext: v for ext, v in sorted(summary.items()) if v}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
