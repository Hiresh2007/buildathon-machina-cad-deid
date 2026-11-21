"""Redact static VRML97 text while preserving its typed geometry and scene graph."""

import math
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

from naming import structural


# A field declaration identifies its VRML type. Node slots additionally constrain
# which node types are valid in that position. Unlisted nodes and fields are omitted.
BOUNDS = {"bboxCenter": "v3", "bboxSize": "v3"}
GROUP = {**BOUNDS, "children": "*children"}
SHAPES = {"Box", "Cone", "Cylinder", "Sphere", "IndexedFaceSet", "IndexedLineSet", "PointSet", "Text"}
CHILDREN = {"Shape", "Group", "Transform", "WorldInfo", "Viewpoint", "NavigationInfo",
            "Anchor", "Inline", "Switch", "LOD", "DirectionalLight", "PointLight", "SpotLight"}
LIGHT = {"ambientIntensity": "f", "color": "v3", "intensity": "f", "on": "bool"}
INDEXED = {"coord": "Coordinate", "coordIndex": "*index", "color": "Color",
           "colorIndex": "*index", "colorPerVertex": "bool"}
SCHEMA = {
    "WorldInfo": {"title": "label", "info": "*label"},
    "Transform": {**GROUP, "translation": "v3", "rotation": "v4", "scale": "v3",
                  "center": "v3", "scaleOrientation": "v4"},
    "Group": GROUP,
    "Switch": {"whichChoice": "int", "choice": "*children"},
    "LOD": {"center": "v3", "range": "*f", "level": "*children"},
    "Anchor": {**GROUP, "url": "*link", "description": "label"},
    "Inline": {**BOUNDS, "url": "*inline"},
    "Viewpoint": {"description": "label", "position": "v3", "orientation": "v4",
                  "fieldOfView": "f", "jump": "bool"},
    "NavigationInfo": {"type": "*navigation", "avatarSize": "*f", "headlight": "bool",
                       "speed": "f", "visibilityLimit": "f"},
    "DirectionalLight": {**LIGHT, "direction": "v3"},
    "PointLight": {**LIGHT, "location": "v3", "attenuation": "v3", "radius": "f"},
    "SpotLight": {**LIGHT, "location": "v3", "direction": "v3", "attenuation": "v3",
                  "radius": "f", "beamWidth": "f", "cutOffAngle": "f"},
    "Shape": {"appearance": "Appearance", "geometry": "geometry"},
    "Appearance": {"material": "Material", "textureTransform": "TextureTransform"},
    "Material": {"ambientIntensity": "f", "diffuseColor": "v3", "emissiveColor": "v3",
                 "specularColor": "v3", "shininess": "f", "transparency": "f"},
    "TextureTransform": {"center": "v2", "rotation": "f", "scale": "v2", "translation": "v2"},
    "Coordinate": {"point": "*v3"}, "Normal": {"vector": "*v3"}, "Color": {"color": "*v3"},
    "TextureCoordinate": {"point": "*v2"},
    "Box": {"size": "v3"}, "Sphere": {"radius": "f"},
    "Cone": {"bottomRadius": "f", "height": "f", "bottom": "bool", "side": "bool"},
    "Cylinder": {"radius": "f", "height": "f", "bottom": "bool", "side": "bool", "top": "bool"},
    "IndexedFaceSet": {**INDEXED, "normal": "Normal", "normalIndex": "*index",
                       "normalPerVertex": "bool", "texCoord": "TextureCoordinate",
                       "texCoordIndex": "*index", "solid": "bool", "ccw": "bool",
                       "convex": "bool", "creaseAngle": "f"},
    "IndexedLineSet": INDEXED,
    "PointSet": {"coord": "Coordinate", "color": "Color"},
    "Text": {"string": "*label", "fontStyle": "FontStyle", "length": "*f", "maxExtent": "f"},
    "FontStyle": {"family": "*family", "style": "style", "justify": "*justify", "size": "f",
                  "spacing": "f", "horizontal": "bool", "leftToRight": "bool", "topToBottom": "bool"},
}
TOKEN = re.compile(r'(?P<space>[ \t\r\n,]+)|(?P<comment>\#[^\r\n]*)|'
                   r'(?P<string>"[^"\\]*(?:\\["\\][^"\\]*)*")|(?P<symbol>[{}\[\]])|'
                   r'(?P<atom>[^\s"#{}\[\],]+)')
HEADER = re.compile(r"\A#VRML V2\.0 utf8(?=[ \t\r\n])[^\r\n]*(?:\r\n|\r|\n)")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
INTEGER = re.compile(r"[+-]?(?:[0-9]+|0[xX][0-9a-fA-F]+)\Z")
IDENT = re.compile(r'[^\x00-\x20"#\x27+,\-.0-9\[\\\]{}\x7f][^\x00-\x20"#\x27,.\[\\\]{}\x7f]*\Z')
RESERVED = {"DEF", "USE", "ROUTE", "PROTO", "EXTERNPROTO", "IS", "NULL", "TO", "TRUE", "FALSE",
            "eventIn", "eventOut", "field", "exposedField"}
GENERATED = re.compile(r"(?:Shape|Transform|Material|Group|Coordinate|Normal|Viewpoint|"
                       r"Camera|Mesh|FaceSet|Color)[_ -]?[0-9]*\Z", re.I)
VIEWS = {"isometric", "axonometric", "perspective", "orthographic", "default view",
         "front view", "top view", "side view", "rear view", "bottom view", "right view", "left view"}
MEASUREMENT = re.compile(
    r"(?i)(?:(?:radius|diameter|length|height|width|depth|pitch|offset|tolerance|"
    r"distance|thickness|scale|angle)\s*[:=]\s*|[Ø⌀R](?=\d))"
    r"[-+±\d.,/ ×x°]+\s*(?:mm|cm|m|in|inch|inches|deg|degrees|rad|µm|um)?\Z"
)
ENUM = {"navigation": {"ANY", "WALK", "EXAMINE", "FLY", "NONE"},
        "family": {"SERIF", "SANS", "TYPEWRITER"}, "style": {"PLAIN", "BOLD", "ITALIC", "BOLDITALIC"},
        "justify": {"BEGIN", "FIRST", "MIDDLE", "END"}}
LABEL = re.compile(r"(?i)((?:author|creator|designer|organization|organisation|company|owner|"
                   r"contact|email|project(?: name| number)?|part(?: name| number)?|model|title|"
                   r"drawn by)\s*[:=]\s*|(?:created|drawn|designed|exported) by\s+)(.+)\Z", re.S)
WIDTH = {"f": 1, "int": 1, "index": 1, "v2": 2, "v3": 3, "v4": 4}


@dataclass
class Array:
    count: int = 0
    largest: int = -1


def neutral(value):
    return bool(structural(value) or MEASUREMENT.fullmatch(value.strip()) or value.casefold() in VIEWS)


def safe_identifier(value):
    # HMAC aliases may include a dimension suffix or a contact domain.
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_-")
                   else f"_{ord(ch):x}_" for ch in value)


def quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Scene:
    def __init__(self, data, alias=None, path=None):
        self.text = data.decode("utf-8")
        header = HEADER.match(self.text)
        if not header or "\x00" in self.text:
            raise ValueError("expected a text VRML97 header")
        self.edits = [(0, header.end(), "#VRML V2.0 utf8\n")] if alias else []
        self.alias, self.path = alias, path
        self.names = set()
        self.defs, self.spellings = {}, {}
        self.declared, self.local_anchors = set(), set()
        self.pos = header.end()
        self.token = None
        self.advance()
        roots = 0
        while self.token:
            self.node("children", 0)
            roots += 1
        if not roots:
            raise ValueError("empty scene")
        if any(key not in self.defs or self.defs[key][0] != "Viewpoint" for key in self.local_anchors):
            raise ValueError("unresolved local viewpoint")

    def advance(self):
        while self.pos < len(self.text):
            token = TOKEN.match(self.text, self.pos)
            if not token:
                raise ValueError("invalid VRML token")
            self.pos = token.end()
            if token.lastgroup == "space":
                continue
            if token.lastgroup == "comment":
                if self.alias:
                    self.edits.append((token.start(), token.end(), ""))
                continue
            self.token = token
            return
        self.token = None

    def take(self, value):
        if self.token and self.token[0] == value:
            self.advance()
            return True
        return False

    def atom(self):
        t = self.token
        if not t or t.lastgroup != "atom":
            raise ValueError("expected a VRML name or number")
        self.advance()
        return t

    def label(self, value):
        if not value.strip() or neutral(value):
            return value
        match = LABEL.fullmatch(value)
        prefix, name = match.groups() if match else ("", value)
        if not name.strip():
            return value
        self.names.add(name)
        return prefix + self.alias(name) if self.alias else value

    def symbol(self, value):
        if not IDENT.fullmatch(value) or value in RESERVED:
            raise ValueError("invalid VRML identifier")
        if neutral(value) or GENERATED.fullmatch(value):
            mapped = value
        else:
            self.names.add(value)
            mapped = safe_identifier(self.alias(value)) if self.alias else value
        if self.spellings.setdefault(mapped, value) != value:
            raise ValueError("distinct node names collide after normalization")
        return mapped

    def edit(self, t, new):
        if self.alias and t[0] != new:
            self.edits.append((t.start(), t.end(), new))

    def link(self, value, inline):
        if not value:
            return value
        if re.search(r"%(?![0-9a-fA-F]{2})", value) or any(ord(ch) < 32 for ch in value):
            raise ValueError("invalid link")
        parts = urlsplit(value)
        if parts.query or parts.scheme or parts.netloc or parts.path.startswith(("/", "\\")):
            raise ValueError("external or active link")
        name = unquote(parts.path, errors="strict").replace("\\", "/")
        if name and not name.lower().endswith(".wrl"):
            raise ValueError("unsupported link target")
        if not name and inline:
            raise ValueError("recursive inline or fragment-only target")
        original_fragment = unquote(parts.fragment, errors="strict")
        fragment = self.symbol(original_fragment) if original_fragment else ""
        if not name and original_fragment:
            self.local_anchors.add(original_fragment)
        target = self.path(name) if self.path and name else name
        return quote(target, safe="/-._~") + ("#" + quote(fragment, safe="-._~") if fragment else "")

    def string(self, kind):
        t = self.token
        if not t or t.lastgroup != "string":
            raise ValueError("expected quoted string")
        self.advance()
        value = re.sub(r'\\(["\\])', r'\1', t[0][1:-1])
        if kind in ENUM:
            if value not in ENUM[kind]:
                raise ValueError("unrecognized enumerated string")
            return
        new = self.label(value) if kind == "label" else self.link(value, kind == "inline")
        self.edit(t, quoted(new))

    def numbers(self, kind, result):
        for _ in range(WIDTH[kind]):
            raw = self.atom()[0]
            if len(raw) > 128:
                raise ValueError("numeric token too large")
            if kind in ("index", "int"):
                if not INTEGER.fullmatch(raw):
                    raise ValueError("expected an integer")
                value = int(raw, 16 if "x" in raw.lower() else 10)
                if not -(1 << 31) <= value < (1 << 31) or (kind == "index" and value < -1):
                    raise ValueError("index or integer out of range")
                result.largest = max(result.largest, value)
            elif not NUMBER.fullmatch(raw) or not math.isfinite(float(raw)):
                raise ValueError("expected finite numeric data")
        result.count += 1

    def field(self, declaration, depth):
        multi = declaration.startswith("*")
        kind = declaration.lstrip("*")
        bracket = multi and self.take("[")
        result = Array()
        while not bracket or not self.take("]"):
            if kind in WIDTH:
                self.numbers(kind, result)
            elif kind in ENUM or kind in {"label", "link", "inline"}:
                self.string(kind)
            elif kind == "bool":
                if not (self.take("TRUE") or self.take("FALSE")):
                    raise ValueError("invalid boolean")
            elif kind == "children":
                self.node(kind, depth)
            else:
                return None if self.take("NULL") else self.node(kind, depth)
            if not bracket:
                break
        return result

    def node(self, expected, depth):
        if depth > 100:
            raise ValueError("scene nesting limit")
        if self.take("USE"):
            name = self.atom()
            self.edit(name, self.symbol(name[0]))
            if name[0] not in self.defs:
                raise ValueError("unresolved USE")
            node, fields = self.defs[name[0]]
        else:
            name = None
            if self.take("DEF"):
                name = self.atom()
                if name[0] in self.declared:
                    raise ValueError("duplicate DEF")
                self.declared.add(name[0])
                self.edit(name, self.symbol(name[0]))
            node = self.atom()[0]
            if node not in SCHEMA or not self.take("{"):
                raise ValueError("unsupported VRML node or statement")
            fields = {}
            while not self.take("}"):
                field = self.atom()[0]
                if field in fields or field not in SCHEMA[node]:
                    raise ValueError("unknown or repeated node field")
                fields[field] = self.field(SCHEMA[node][field], depth + 1)
            if node in ("IndexedFaceSet", "IndexedLineSet"):
                for ix, slot, attr in (("coordIndex", "coord", "point"), ("colorIndex", "color", "color"),
                                       ("normalIndex", "normal", "vector"), ("texCoordIndex", "texCoord", "point")):
                    index = fields.get(ix)
                    if index and index.largest >= 0:
                        target = fields.get(slot)
                        if not target or index.largest >= target[1].get(attr, Array()).count:
                            raise ValueError("geometry index points outside its array")
            if name is not None:
                self.defs[name[0]] = node, fields
        allowed = CHILDREN if expected == "children" else SHAPES if expected == "geometry" else {expected}
        if node not in allowed:
            raise ValueError("node is incompatible with field")
        return node, fields

    def output(self):
        chunks, pos = [], 0
        for start, end, new in sorted(self.edits):
            chunks.extend((self.text[pos:start], new))
            pos = end
        chunks.append(self.text[pos:])
        return "".join(chunks).encode("utf-8")


def discover(data):
    return Scene(data).names


def rewrite(data, alias, redact_text, rewrite_path):
    return Scene(data, alias, rewrite_path).output()
