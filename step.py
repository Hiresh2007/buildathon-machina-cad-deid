"""A token-preserving reader and rewriter for self-contained Part 21 STEP files."""

import re


# Records containing only neutral empty labels, numbers, enums or references
# have no strings to rewrite. Parse these common records with a bounded grammar.
# All other records use the field and section parser below.
NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?"
SCALAR = r"(?:''|#[0-9]+|\$|\*|\.[A-Za-z][A-Za-z0-9_]*\.|" + NUMBER + r")"
SCALAR_LIST = r"\(\s*" + SCALAR + r"(?:\s*,\s*" + SCALAR + r")*\s*\)"
FIELD = r"(?:" + SCALAR_LIST + r"|" + SCALAR + r")"
SIMPLE = (r"(?P<record>\#(?P<number>[0-9]+)\s*=\s*[A-Za-z][A-Za-z_0-9]*\s*\(\s*"
          r"(?P<arguments>" + FIELD + r"(?:\s*,\s*" + FIELD + r"){0,9})\s*\)\s*;)")
REFERENCE = re.compile(r"#([0-9]+)")
TOKEN = re.compile(SIMPLE + r"|" +
    r"'(?:''|[^'])*'|/\*[\s\S]*?\*/|\"[0-9A-Fa-f]*\"|#[0-9]+|"
    r"[A-Za-z_][A-Za-z_0-9-]*|" + NUMBER + r"|\s+|."
)
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9-]*\Z")
UNESCAPE = re.compile(r"\\X([24])\\([0-9a-fA-F]+)\\X0\\|\\X\\([0-9a-fA-F]{2})|\\\\")
EXTENSION = re.compile(r"\.(?:step|stp|igs|iges|dxf|dwg|ipt|iam|idw|sldprt|sldasm|slddrw|pdf)\Z", re.I)
PROPERTY_KEYS = {
    "author", "company", "company name", "customer", "created by", "designer", "drawn by",
    "organization", "owner", "project name", "project number", "part number", "email", "email address",
}
ENGINEERING_KEYS = {"material", "thickness", "length", "diameter", "radius", "tolerance",
                    "volume", "area", "mass", "density", "units", "weight", "finish"}
NEUTRAL = {"", "none", "unknown", "unspecified", "default", "n/a", "part", "assembly", "product", "design"}
STRONG_FIELDS = {
    "FILE_NAME": {2, 3, 6}, "PERSON": {0, 1, 2, 3}, "ORGANIZATION": {0, 1, 2},
    "PRODUCT": {0, 1}, "DOCUMENT": {0, 1}, "DOCUMENT_FILE": {0, 1},
    "NEXT_ASSEMBLY_USAGE_OCCURRENCE": {0, 1}, "ASSEMBLY_COMPONENT_USAGE": {0, 1},
}


def _decode(value):
    def escape(match):
        if match[1]:
            raw = bytes.fromhex(match[2])
            return raw.decode("utf-16-be" if match[1] == "2" else "utf-32-be")
        if match[3]:
            return bytes.fromhex(match[3]).decode("latin1")
        return "\\"
    return UNESCAPE.sub(escape, value[1:-1].replace("''", "'"))


def _quote(value):
    def letter(c):
        if 32 <= ord(c) <= 126:
            return {"'": "''", "\\": "\\\\"}.get(c, c)
        return "\\X2\\" + c.encode("utf-16-be").hex().upper() + "\\X0\\"
    return "'" + "".join(map(letter, value)) + "'"


class Frame:
    def __init__(self, name):
        self.name = name
        self.field = 0
        self.label = ""


def _role(frames, value):
    for f in reversed(frames):
        if f.name == "FILE_SCHEMA" or (f.name == "FILE_NAME" and f.field == 1):
            return "keep"
        if f.name in {"CONVERSION_BASED_UNIT", "SI_UNIT", "APPLICATION_PROTOCOL_DEFINITION"}:
            return "keep"
        if f.name == "DESCRIPTIVE_REPRESENTATION_ITEM":
            if f.field == 0 and value.strip().casefold() in PROPERTY_KEYS | ENGINEERING_KEYS:
                return "keep"
            if f.field == 1 and f.label in ENGINEERING_KEYS:
                return "keep"
        if f.name == "FILE_DESCRIPTION" and f.field == 1:
            return "keep"
        if f.name == "FILE_NAME" and f.field == 0:
            return "path"
        if f.name in {"ADDRESS", "PERSONAL_ADDRESS", "ORGANIZATIONAL_ADDRESS"}:
            return "identity"
        if f.field in STRONG_FIELDS.get(f.name, ()):
            return "identity"
        if f.name == "DESCRIPTIVE_REPRESENTATION_ITEM" and f.field == 1 and f.label in PROPERTY_KEYS:
            return "identity"
    if EXTENSION.search(value) or (frames and frames[0].name == "EXTERNAL_SOURCE"):
        return "path"
    return "text"


def _scan(data, handler, emit):
    """Validate section nesting and all #references as strings are visited."""
    try:
        source, encoding = data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        source, encoding = data.decode("latin1"), "latin1"
    state = "start"
    required_headers = {"FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA"}
    defined, used = set(), set()
    word, head, previous, stack = None, None, None, []
    record_id = None
    body = False
    output = [] if emit else None
    for match in TOKEN.finditer(source):
        if match['record'] is not None:
            current = int(match['number'])
            if state != 'data' or head is not None or stack or current in defined:
                raise ValueError('unexpected or duplicate geometry record')
            defined.add(current)
            used.update(map(int, REFERENCE.findall(match['arguments'])))
            if emit:
                output.append(match[0])
            word, previous = None, ';'
            continue
        token = match[0]
        result = token
        if token.isspace() or token.startswith("/*"):
            if token.startswith("/*"):
                result = " " + "\n" * token.count("\n")
        else:
            if state == "done":
                raise ValueError("trailing data")
            if head is None:
                head = token.upper()
                record_id = int(token[1:]) if token.startswith("#") else None
                body = False
            elif token.startswith("#"):
                used.add(int(token[1:]))
            if token.startswith("'"):
                if len(token) < 2 or token[-1] != "'" or not stack:
                    raise ValueError("invalid quoted field")
                value = _decode(token)
                role = _role(stack, value)
                replacement = handler(role, value)
                if emit and replacement != value:
                    result = _quote(replacement)
                if stack[-1].field == 0:
                    stack[-1].label = value.strip().casefold()
            elif token == "(":
                stack.append(Frame(word))
            elif token == ",":
                if not stack:
                    raise ValueError("field outside entity")
                stack[-1].field += 1
            elif token == ")":
                if not stack:
                    raise ValueError("unbalanced entity")
                stack.pop()
            elif token == "=" and record_id is not None and previous == head:
                body = True
            elif token == ";":
                if stack:
                    raise ValueError("unterminated entity")
                if state == "start" and head == "ISO-10303-21":
                    state = "preheader"
                elif state == "preheader" and head == "HEADER":
                    state = "header"
                elif state == "header" and head == "ENDSEC" and not required_headers:
                    state = "predata"
                elif state == "header" and IDENTIFIER.fullmatch(head) and head != "ENDSEC":
                    required_headers.discard(head)
                elif state in {"predata", "postdata"} and head == "DATA":
                    state = "data"
                elif state == "data" and head == "ENDSEC":
                    state = "postdata"
                elif state == "data" and record_id is not None and body and record_id not in defined:
                    defined.add(record_id)
                elif state == "postdata" and head == "END-ISO-10303-21":
                    state = "done"
                else:
                    raise ValueError("invalid or unsupported STEP section")
                head = None
            elif token in {"'", '"', "/"}:
                raise ValueError("invalid token")
            word = token.upper() if IDENTIFIER.fullmatch(token) else None
            previous = token.upper()
        if emit:
            output.append(result)
    if state != "done" or head is not None or not defined or not used.issubset(defined):
        raise ValueError("incomplete STEP data or dangling reference")
    return "".join(output).encode(encoding) if emit else None


def discover(data):
    identities = set()

    def visit(role, value):
        if role == "identity" and value.strip().casefold() not in NEUTRAL:
            identities.add(value)
        return value

    _scan(data, visit, False)
    return identities


def rewrite(data, alias, redact_text, rewrite_path):
    def replace(role, value):
        if role == "keep" or value.strip().casefold() in NEUTRAL:
            return value
        if role == "identity":
            return alias(value)
        if role == "path":
            return rewrite_path(value)
        return redact_text(value)
    return _scan(data, replace, True)
