"""Read typed identity properties in the directory-based SOLIDWORKS native container."""
import struct
import zlib
from xml.etree import ElementTree as ET

from naming import structural
from pdf_identity import Observations

CORE = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
DC = "{http://purl.org/dc/elements/1.1/}"
EXT = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
PROPERTIES = {
    "docProps/core.xml": (CORE + "coreProperties", {
        DC + "creator": "person", CORE + "lastModifiedBy": "person", DC + "lastModifiedBy": "person",
    }),
    "docProps/app.xml": (EXT + "Properties", {EXT + "Company": "organization", EXT + "Manager": "person"}),
}
DEFAULTS = {"none", "default", "user", "administrator", "autodesk", "inventor", "solidworks", "unknown",
            "not specified", "n/a", "microsoft", "system", "admin", "owner", "guest", "operator",
            "student", "designer", "engineer", "root", "company name", "your company name",
            "dassault systèmes solidworks corporation", "dassault systemes solidworks corporation"}
NAME_BYTES = bytes((byte >> 4) | ((byte & 15) << 4) for byte in range(256))
MAX_METADATA = 2 * 1024 * 1024


def _directory(data, start, end, count):
    """Validate central records against their local headers before trusting a named property."""
    central_signature = data[start:start + 4]
    local_signature = None
    properties = {}
    seen_names = set()
    cursor = start
    for _ in range(count):
        if cursor + 46 > end or data[cursor:cursor + 4] != central_signature:
            raise ValueError("invalid native CAD central record")
        crc, packed, size = struct.unpack_from("<3I", data, cursor + 16)
        length, extra, comment = struct.unpack_from("<3H", data, cursor + 28)
        local = 8 + struct.unpack_from("<I", data, cursor + 42)[0]
        advance = 46 + length + extra + comment
        if not 0 < length <= 1024 or cursor + advance > end or local + 30 + length > start:
            raise ValueError("invalid native CAD stream bounds")
        if local_signature is None:
            local_signature = data[local:local + 4]
        if (local_signature == central_signature or data[local:local + 4] != local_signature
                or data[local + 4:local + 10] != data[cursor + 6:cursor + 12]
                or data[local + 14:local + 28] != data[cursor + 16:cursor + 30]):
            raise ValueError("native CAD local and central headers differ")
        name = data[cursor + 46:cursor + 46 + length]
        if name in seen_names or data[local + 30:local + 30 + length] != name:
            raise ValueError("inconsistent or duplicate native CAD stream name")
        seen_names.add(name)
        begin = local + 30 + length + struct.unpack_from("<H", data, local + 28)[0]
        if begin + packed > start:
            raise ValueError("invalid native CAD compressed payload")
        decoded = name.translate(NAME_BYTES).decode("ascii", "backslashreplace")
        if decoded in PROPERTIES:
            method = struct.unpack_from("<H", data, local + 8)[0]
            properties[decoded] = (begin, packed, size, crc, method)
        cursor += advance
    return properties


def property_streams(data):
    if len(data) < 64 or data[4:8] != bytes((0, 0, 0, 4)):
        raise ValueError("unsupported native CAD container")
    # Directory offsets are relative to the eight-byte file header. Validate a
    # complete directory because filler can contain other end-record signatures.
    for end in range(len(data) - 22, max(8, len(data) - 65557) - 1, -1):
        if data[end + 4:end + 8] != bytes(4) or data[end + 20:end + 22] != bytes(2):
            continue
        count, repeated, length, relative = struct.unpack_from("<HHII", data, end + 8)
        start = 8 + relative
        if not 0 < count <= 4096 or count != repeated or not 8 <= start < end or start + length != end:
            continue
        try:
            return _directory(data, start, end, count)
        except ValueError:
            continue
    raise ValueError("no complete native CAD directory")


def _read_property(data, entry):
    begin, packed, size, crc, method = entry
    if max(packed, size) > MAX_METADATA:
        raise ValueError("native CAD identity metadata exceeds size bound")
    body = data[begin:begin + packed]
    if method == 8:
        decoder = zlib.decompressobj(-15)
        try:
            body = decoder.decompress(body, size + 1)
        except zlib.error as exc:
            raise ValueError("invalid compressed native CAD property") from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid compressed native CAD property length")
    elif method != 0:
        raise ValueError("unsupported native CAD property compression")
    if len(body) != size or zlib.crc32(body) & 0xffffffff != crc:
        raise ValueError("native CAD property checksum mismatch")
    return body


def discover(data):
    found = Observations()
    for name, entry in property_streams(data).items():
        body = _read_property(data, entry)
        if b"<!doctype" in body.replace(b"\0", b"").lower():
            raise ValueError("native CAD properties cannot declare a DTD")
        try:
            document = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ValueError("invalid native CAD property XML") from exc
        root, fields = PROPERTIES[name]
        if document.tag != root:
            continue
        for element in document:
            role = fields.get(element.tag)
            value = (element.text or "").strip()
            if (role and 3 <= len(value) <= 256 and value.casefold() not in DEFAULTS
                    and not structural(value) and any(char.isalpha() for char in value)):
                found.add(value)
                found.observe(role, value)
                found.observe_hosts(value)
    return found.finish()
