"""Pseudonymize SVG text and document metadata while retaining graphic coordinates and IDs."""
import re
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError

SVG = "http://www.w3.org/2000/svg"
DC = {"http://purl.org/dc/elements/1.1/", "http://purl.org/dc/terms/"}
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
LABEL = re.compile(r"(?i)^\s*(?:drawn by|checked by|approved by|designed by|author|company|customer|project name|drawing number|part number)\s*[:=]\s*(\S.{0,125}?)\s*$")
IDENTITY = {"creator", "publisher", "contributor", "author", "company", "owner", "customer"}
VISUAL = {"text", "tspan", "textPath", "title", "desc", "metadata"}


def _document(data):
    if re.search(rb"<!ENTITY\s", data, re.I):
        raise ValueError("custom SVG entities require separate review")
    try:
        doc = minidom.parseString(data)
    except (ExpatError, ValueError, UnicodeError) as exc:
        raise ValueError("unreadable SVG XML") from exc
    if doc.documentElement.localName != "svg" or doc.documentElement.namespaceURI not in (None, SVG):
        raise ValueError("expected SVG root")
    return doc


def _values(node, in_text=False, identity=False):
    if node.nodeType in (Node.TEXT_NODE, Node.CDATA_SECTION_NODE, Node.COMMENT_NODE):
        if (in_text or node.nodeType == Node.COMMENT_NODE) and node.data.strip():
            yield node, "identity" if identity else "text", node.data
        return
    if node.nodeType != Node.ELEMENT_NODE:
        return
    name = node.localName
    in_text = in_text or name in VISUAL
    identity = identity or (node.namespaceURI in DC and name.casefold() in IDENTITY)
    for attr in node.attributes.values():
        key = attr.localName.casefold()
        value = attr.value
        if key in ("docname", "export-filename") or (key in ("href", "src") and name in ("image", "use", "a", "feImage")):
            if value and not value.startswith(("#", "data:")):
                yield attr, "reference", value
        elif key in IDENTITY:
            yield attr, "identity", value
        elif key in ("label", "aria-label", "title"):
            yield attr, "text", value
    for child in node.childNodes:
        yield from _values(child, in_text, identity)


def discover(data):
    with _document(data) as doc:
        for _, role, text in _values(doc.documentElement):
            if role == "identity":
                yield text.strip()
            label = LABEL.fullmatch(text)
            if label:
                yield label[1].strip()
            yield from EMAIL.findall(text)


def rewrite(data, alias, redact_text, rewrite_path):
    changed = False
    with _document(data) as doc:
        for node, role, value in _values(doc.documentElement):
            if role == "identity" and value.strip():
                revised = value.replace(value.strip(), alias(value.strip()), 1)
            elif role == "reference" and not re.match(r"^[A-Za-z][A-Za-z\d+.-]*://", value):
                path, sep, fragment = value.partition("#")
                revised = rewrite_path(path) + sep + fragment
            else:
                revised = redact_text(value)
            if revised != value:
                if node.nodeType == Node.ATTRIBUTE_NODE:
                    node.value = revised
                else:
                    node.data = revised
                changed = True
        return doc.toxml(encoding="utf-8") if changed else data
