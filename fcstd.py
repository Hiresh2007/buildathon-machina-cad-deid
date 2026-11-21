"""Pseudonymize typed FreeCAD document properties while keeping shape streams and object IDs."""
import copy
import io
import re
import zipfile
from xml.dom import minidom
from xml.parsers.expat import ExpatError

import svg


IDENTITY = {"createdby", "lastmodifiedby", "company", "author", "designer", "approvedby",
            "customer", "projectnumber", "partnumber", "drawingnumber", "manufacturer"}
NEUTRAL = {"freecad", "default", "unknown", "unspecified", "none", "n/a"}
TEXT = {"comment", "description", "label", "label2", "projectname", "title"}
DOCS = {"Document.xml", "GuiDocument.xml"}
MAX_UNPACKED = 256 * 1024 * 1024


def _archive(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid FreeCAD container") from exc
    members = z.infolist()
    if ("Document.xml" not in z.namelist() or len(z.namelist()) != len(set(z.namelist()))
            or any(m.flag_bits & 1 for m in members) or sum(m.file_size for m in members) > MAX_UNPACKED):
        z.close()
        raise ValueError("unsupported FreeCAD archive layout")
    return z


def _document(raw):
    if re.search(rb"<!ENTITY\s", raw, re.I):
        raise ValueError("external FreeCAD XML definitions require review")
    try:
        return minidom.parseString(raw)
    except (ExpatError, UnicodeError) as exc:
        raise ValueError("invalid FreeCAD document XML") from exc


def _fields(doc):
    for prop in doc.getElementsByTagName("Property"):
        name = prop.getAttribute("name").casefold()
        kind = prop.getAttribute("type")
        if kind == "App::PropertyString":
            role = "identity" if name in IDENTITY else "path" if name == "filename" else "text" if name in TEXT else None
        elif kind in {"App::PropertyFile", "App::PropertyPath"}:
            role = "path"
        else:
            role = None
        if role:
            for child in prop.childNodes:
                if child.nodeType == child.ELEMENT_NODE and child.tagName == "String" and child.hasAttribute("value"):
                    yield child, role, child.getAttribute("value")
        if name == "meta" and kind == "App::PropertyMap":
            for item in prop.getElementsByTagName("Item"):
                key = item.getAttribute("key").casefold()
                if item.hasAttribute("value"):
                    yield item, "identity" if key in IDENTITY else "text", item.getAttribute("value")


def _read(archive, member):
    try:
        return archive.read(member)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise ValueError("unreadable FreeCAD member") from exc


def discover(data):
    with _archive(data) as z:
        for name in z.namelist():
            if name in DOCS:
                with _document(_read(z, name)) as doc:
                    for _, role, value in _fields(doc):
                        if role == "identity" and value.strip() and value.strip().casefold() not in NEUTRAL:
                            yield value.strip()
                        yield from svg.EMAIL.findall(value)
            elif name.lower().endswith(".svg"):
                yield from svg.discover(_read(z, name))


def rewrite(data, alias, redact_text, rewrite_path):
    changed = {}
    with _archive(data) as z:
        members = set(z.namelist())
        for name in members:
            if name in DOCS:
                with _document(_read(z, name)) as doc:
                    edited = False
                    for node, role, value in _fields(doc):
                        if not value.strip():
                            continue
                        if role == "identity":
                            out = value if value.strip().casefold() in NEUTRAL else alias(value.strip())
                        elif role == "path":
                            out = value if value in members else rewrite_path(value)
                        else:
                            out = redact_text(value)
                        if out != value:
                            node.setAttribute("value", out)
                            edited = True
                    if edited:
                        changed[name] = doc.toxml(encoding="utf-8")
            elif name.lower().endswith(".svg"):
                raw = _read(z, name)
                out = svg.rewrite(raw, alias, redact_text, lambda path: path if path in members else rewrite_path(path))
                if out != raw:
                    changed[name] = out
        if not changed and not z.comment and all(not m.comment for m in z.infolist()):
            return data
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as result:
            for item in z.infolist():
                info = copy.copy(item)
                info.comment = b""
                raw = changed[item.filename] if item.filename in changed else _read(z, item)
                result.writestr(info, raw)
        return buf.getvalue()
