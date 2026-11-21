"""Rewrite supported textual metadata in ACIS SAT without rebuilding B-rep records."""
import re


FIRST = re.compile(rb"^[ \t]*(\d+)[ \t]+(\d+)[ \t]+\d+(?:[ \t]+\d+)?[ \t]*$")
COUNT = re.compile(rb"[ \t]*(\d+)[ \t]")
RECORD = re.compile(rb"^\s*(?:-\d+\s+)?([a-zA-Z][a-zA-Z0-9_-]*)\s")
TOKEN = re.compile(rb"(?<!\S)@(\d+)[ \t]")
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
LABEL = re.compile(r"(?i)\b(?:author|company|organization|project name|part number|customer)\s*[:=]\s*(.+?)\s*$")
IDENTITY = re.compile(r"(?:^|-)(?:author|organization|company|customer|project|part-name|part-number|drawing-number)(?:-|$)")
REFERENCE = re.compile(r"(?:^|-)(?:filename|file-name|external-ref|xref|source-path)(?:-|$)")


def _text(raw):
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _fields(data):
    if b"\x00" in data or len(data) < 10:
        raise ValueError("binary or incomplete ACIS input")
    rows = data.splitlines(keepends=True)
    header = FIRST.fullmatch(rows[0].rstrip(b"\r\n"))
    if not header or (int(header[2]) == 0 and b"End-of-" not in data[-4096:]):
        raise ValueError("unrecognized SAT header or missing end record")
    offset = len(rows[0])
    first_record = 1
    if int(header[1]) >= 200:
        if len(rows) < 3:
            raise ValueError("truncated SAT header")
        line = rows[1].rstrip(b"\r\n")
        pos = 0
        for index in range(3):
            match = COUNT.match(line, pos)
            if not match:
                raise ValueError("unrecognized SAT header string")
            lo, hi = match.end(), match.end() + int(match[1])
            if hi > len(line):
                raise ValueError("truncated SAT header string")
            if index == 0:
                yield offset + match.start(1), offset + hi, line[lo:hi], "free"
            pos = hi
        if line[pos:].strip():
            raise ValueError("unrecognized SAT header suffix")
        first_record = 3
        offset = sum(map(len, rows[:first_record]))
    for row in rows[first_record:]:
        if b"attrib" in row[:160]:
            name = RECORD.match(row)
            if name:
                kind = name[1].decode("ascii").casefold()
                if "attrib" in kind and "material" not in kind:
                    role = "identity" if IDENTITY.search(kind) else "reference" if REFERENCE.search(kind) else "free"
                    pos = name.end()
                    while (token := TOKEN.search(row, pos)) is not None:
                        lo, hi = token.end(), token.end() + int(token[1])
                        if hi > len(row.rstrip(b"\r\n")) or hi < len(row) and not row[hi:hi + 1].isspace():
                            break
                        yield offset + token.start(1), offset + hi, row[lo:hi], role
                        pos = hi
        offset += len(row)


def discover(data):
    for _, _, raw, role in _fields(data):
        text, _ = _text(raw)
        if role == "identity" and text.strip():
            yield text.strip()
        label = LABEL.search(text)
        if label:
            yield label[1].strip()
        yield from EMAIL.findall(text)


def rewrite(data, alias, redact_text, rewrite_path):
    parts, end = [], 0
    for start, stop, raw, role in _fields(data):
        text, encoding = _text(raw)
        revised = alias(text) if role == "identity" and text.strip() else (
            rewrite_path(text) if role == "reference" and text.strip() else redact_text(text))
        if revised != text:
            out = revised.encode(encoding)
            parts.extend((data[end:start], str(len(out)).encode(), b" ", out))
            end = stop
    if not parts:
        return data
    parts.append(data[end:])
    return b"".join(parts)
