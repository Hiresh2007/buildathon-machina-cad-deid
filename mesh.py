"""Pseudonymize STL model names without changing triangle or color records."""

import re
import struct

from naming import structural


NUMBER = rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
TRIPLE = rb"\s+".join([NUMBER] * 3)
FACET = re.compile(
    rb"facet\s+normal\s+"
    + TRIPLE
    + rb"\s+outer\s+loop\s+"
    + (rb"vertex\s+" + TRIPLE + rb"\s+") * 3
    + rb"endloop\s+endfacet\b\s*",
    re.I,
)
NAME = re.compile(rb"(?im)^[ \t]*(solid|endsolid)\b([^\r\n]*)(?:\r\n|\r|\n|$)")
COLORS = re.compile(rb"COLOR=.{4}(?:,?MATERIAL=.{12})?", re.S)


def _binary(data):
    return (
        len(data) >= 84 and len(data) == 84 + 50 * struct.unpack_from("<I", data, 80)[0]
    )


def _binary_name(data):
    return COLORS.sub(b"", data[:80]).strip(b" \t\0\r\n").decode("latin1")


def _ascii(data):
    offset = 0
    opened = False
    found = False
    for record in NAME.finditer(data):
        segment = data[offset : record.start()]
        if opened:
            cursor = len(segment) - len(segment.lstrip())
            while cursor < len(segment):
                facet = FACET.match(segment, cursor)
                if not facet:
                    raise ValueError("invalid ASCII STL facet")
                cursor = facet.end()
        elif segment.strip():
            raise ValueError("data outside STL solid")
        start = record[1].lower() == b"solid"
        if start == opened:
            raise ValueError("invalid STL solid nesting")
        opened, found, offset = start, True, record.end()
        raw = record[2].strip()
        if raw:
            yield record.start(2) + len(record[2]) - len(record[2].lstrip()), raw
    if opened or not found or data[offset:].strip():
        raise ValueError("incomplete ASCII STL")


def discover(data):
    if _binary(data):
        name = _binary_name(data)
        if name and not structural(name):
            yield name
    else:
        for _, raw in _ascii(data):
            name = raw.decode("utf-8", "replace")
            if not structural(name):
                yield name


def rewrite(data, alias, redact_text, rewrite_path):
    if _binary(data):
        name = _binary_name(data)
        color = b" ".join(m[0] for m in COLORS.finditer(data[:80]))
        display = name if structural(name) else alias(name) if name else ""
        header = display.encode("ascii") + (b" " + color if color else b"")
        if len(header) > 80:
            raise ValueError("unsupported binary STL color header")
        return header.ljust(80, b"\0") + data[80:]
    result = bytearray()
    previous = 0
    for offset, raw in _ascii(data):
        result.extend(data[previous:offset])
        name = raw.decode("utf-8", "replace")
        result.extend(raw if structural(name) else alias(name).encode("utf-8"))
        previous = offset + len(raw)
    result.extend(data[previous:])
    return bytes(result)
