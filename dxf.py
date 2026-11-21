"""Edit identifying fields in text DXF while preserving drawing and handle records."""

import codecs
import re


IDENTITY_TAGS = {
    "AUTHOR", "DRAWNBY", "DRAWN", "DRAFTER", "DESIGNER", "DESIGNEDBY", "CHECKEDBY",
    "APPROVEDBY", "COMPANY", "COMPANYNAME", "CUSTOMER", "CUSTOMERNAME", "CLIENT",
    "EMAIL", "ADDRESS", "PROJECTNAME", "PROJECTNO", "PROJECTNUMBER", "PARTNUMBER",
    "PARTNO", "DRAWINGNUMBER", "DRAWINGNO",
}
REFERENCE_TYPES = {"IMAGEDEF", "PDFDEFINITION", "DWFDEFINITION", "DGNDEFINITION", "BLOCK"}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _tag(text):
    return re.sub("[^A-Z0-9]", "", text.upper())


def _pairs(data):
    if b"\0" in data:
        raise ValueError("binary DXF requires a native handler")
    # Split only on DXF record endings. High bytes and formatting controls can occur in text.
    lines = re.findall(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", data)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) % 2:
        raise ValueError("incomplete DXF tag")
    try:
        codes = [int(line.strip()) for line in lines[::2]]
    except ValueError:
        raise ValueError("invalid DXF code") from None
    if not codes or codes[-1] != 0 or lines[-1].strip() != b"EOF":
        raise ValueError("missing DXF end marker")
    values = [v.rstrip(b"\n\r") for v in lines[1::2]]
    encoding, version = "cp1252", b""
    for i, code in enumerate(codes[:-1]):
        if code != 9:
            continue
        if values[i] == b"$ACADVER":
            version = values[i + 1].strip()
        if values[i] == b"$DWGCODEPAGE" and values[i + 1].startswith(b"ANSI_"):
            encoding = "cp" + values[i + 1][5:].decode("ascii", "ignore").strip()
    if version >= b"AC1021":
        encoding = "utf-8"
    try:
        codecs.lookup(encoding)
    except LookupError:
        raise ValueError("unsupported DXF text encoding") from None
    return lines, codes, [value.decode(encoding, "surrogateescape") for value in values], encoding


def _fields(codes, values):
    """Yield (value tag, action) for text fields only. A handle is never a text field."""
    current_section = None
    starts = [i for i, code in enumerate(codes) if code == 0]
    if not starts:
        raise ValueError("missing DXF entities")
    for start, stop in zip(starts, starts[1:] + [len(codes)]):
        kind = values[start].strip().upper()
        fields = range(start + 1, stop)
        if kind == "SECTION":
            current_section = next((values[i].strip() for i in fields if codes[i] == 2), None)
            header = custom_tag = None
            if current_section == "HEADER":
                for i in fields:
                    if codes[i] == 9:
                        header = values[i]
                    elif header == "$CUSTOMPROPERTYTAG" and codes[i] == 1:
                        custom_tag = _tag(values[i])
                    elif codes[i] == 1 and (header == "$LASTSAVEDBY" or
                            (header == "$CUSTOMPROPERTY" and custom_tag in IDENTITY_TAGS)):
                        yield i, "identity"
            continue
        if kind == "ENDSEC":
            current_section = None
            continue
        attr = next((_tag(values[i]) for i in fields if codes[i] == 2), "") if kind in {"ATTRIB", "ATTDEF"} else ""
        for i in fields:
            code = codes[i]
            if code == 1 and kind in REFERENCE_TYPES and values[i].strip():
                yield i, "path"
            elif code == 1 and attr in IDENTITY_TAGS:
                yield i, "identity"
            elif (kind in {"TEXT", "ATTRIB", "ATTDEF", "DIMENSION"} and code == 1
                  or kind == "MTEXT" and code in {1, 3} or code == 999):
                yield i, "text"


def _meaningful(value):
    return bool(value and any(c.isalnum() for c in value) and value.upper() not in
                {"N/A", "NONE", "UNKNOWN", "TBD", "BYLAYER", "BYBLOCK"} and not value.startswith("%<"))


def discover(data):
    _, codes, values, _ = _pairs(data)
    for i, action in _fields(codes, values):
        value = values[i].strip()
        if action == "identity" and _meaningful(value):
            yield value
        if action != "path":
            yield from EMAIL.findall(value)


def rewrite(data, alias, redact_text, rewrite_path):
    lines, codes, values, encoding = _pairs(data)
    changed = False
    for i, action in _fields(codes, values):
        value = values[i]
        if action == "path":
            replacement = rewrite_path(value)
        elif action == "identity" and _meaningful(value.strip()):
            replacement = alias(value.strip())
        else:
            replacement = redact_text(value)
        if replacement == value:
            continue
        if "\n" in replacement or "\r" in replacement:
            raise ValueError("replacement spans multiple DXF tags")
        line_index = 2 * i + 1
        original = lines[line_index]
        ending = original[len(original.rstrip(b"\r\n")):]
        lines[line_index] = replacement.encode(encoding, "surrogateescape") + ending
        changed = True
    return b"".join(lines) if changed else data
