"""Edit PDF text, metadata, references and supported CAD attachments in their native containers."""

import re
import hashlib
from contextlib import contextmanager
from pathlib import PurePosixPath
from xml.dom import Node, minidom
from xml.parsers.expat import ExpatError

import dxf
import mesh
import pdf_identity
import step
from naming import structural


IDENTITY = {
    "author",
    "creator",
    "company",
    "organization",
    "organisation",
    "publisher",
    "contributor",
    "owner",
    "designer",
    "client",
    "customer",
    "title",
    "project",
    "drawnby",
    "checkedby",
    "approvedby",
    "partnumber",
    "drawingnumber",
    "createdby",
    "modifiedby",
    "lastsavedby",
}
PROTECTED = {
    "date",
    "createdate",
    "modifydate",
    "metadatadate",
    "format",
    "version",
    "documentid",
    "instanceid",
    "originaldocumentid",
    "creationdate",
    "moddate",
}
LABEL = re.compile(
    r"^\s*(?P<label>drawn(?:\s+by)?|checked(?:\s+by)?|approved(?:\s+by)?|"
    r"designed(?:\s+by)?|author|client|customer|company|organization|project(?:\s+(?:name|no\.?|number))?|"
    r"(?:part|drawing)\s+(?:no\.?|number)|email|e-mail|telephone|phone)\s*[:=]\s*(?P<value>.+?)\s*$",
    re.I,
)
PERSON_LABELS = {
    "drawn",
    "drawn by",
    "checked",
    "checked by",
    "approved",
    "approved by",
    "designed by",
    "author",
}
COMPANY_LABELS = {"company", "customer", "client", "organization"}
CORPORATE = re.compile(
    r"^[^\d\n:;]{2,64}\b(?:inc\.?|ltd\.?|limited|llc|gmbh|corp\.?|corporation|s\.?a\.?r\.?l\.?)$",
    re.I,
)
TECHNICAL = {
    "assembly",
    "material",
    "mass",
    "scale",
    "angle",
    "name",
    "item",
    "finish",
    "tolerance",
    "tolerances",
    "date",
    "title",
    "sheet",
    "drawing",
    "part",
    "quantity",
    "revision",
    "rev",
    "mm",
    "units",
    "size",
    "steel",
    "aluminum",
    "aluminium",
    "plastic",
    "sheet",
    "projection",
    "surface",
    "default",
    "metric",
    "approved",
    "checked",
    "drawn",
    "customer",
    "company",
    "project",
    "unless",
    "specified",
    "dimensions",
    "description",
    "number",
    "by",
    "of",
    "not",
    "font",
    "third",
    "inch",
    "inches",
    "general",
    "linear",
}
MEMBERS = {".step": step, ".stp": step, ".dxf": dxf, ".stl": mesh}
ATTACH_MAX = 32 * 1024 * 1024


class UnsupportedPDF(ValueError):
    """A neutral, safe-to-report reason for omitting a PDF."""


def library():
    try:
        import pymupdf
    except ImportError as error:
        raise ValueError(
            "PDF support requires python -m pip install -r requirements.txt"
        ) from error
    return pymupdf


@contextmanager
def document(data):
    pdf = library()
    try:
        with pdf.open(stream=data, filetype="pdf") as doc:
            if (
                doc.needs_pass
                or doc.get_sigflags() > 0
                or not 0 < len(doc) <= 1024
                or doc.xref_length() > 150000
            ):
                raise UnsupportedPDF("encrypted, signed, empty or oversized PDF")
            if doc.is_form_pdf:
                raise UnsupportedPDF("interactive PDF form")
            yield doc
    except (RuntimeError, OverflowError, ExpatError, pdf.mupdf.FzErrorBase) as error:
        raise UnsupportedPDF("unsupported PDF object") from error


def meaningful(value):
    return bool(
        value.strip()
        and any(c.isalnum() for c in value)
        and value.strip().casefold() not in step.NEUTRAL
    )


def field(key, value, alias, redact):
    kind = re.sub(r"[\s_-]+", "", key.casefold())
    if (
        kind in PROTECTED
        or not meaningful(value)
        or (kind == "title" and structural(value))
    ):
        return value
    if kind in IDENTITY:
        if kind in pdf_identity.PERSON_FIELDS or kind == "title":
            return pdf_identity.typed("title" if kind == "title" else "person", value.strip(), alias)
        return alias(value.strip())
    return redact(value)


def xmp(source, alias, redact):
    if not source:
        return source
    marker = (
        source.replace(b"\0", b"").upper()
        if isinstance(source, bytes)
        else source.upper().encode()
    )
    if len(source) > 4 * 1024 * 1024 or b"<!DOCTYPE" in marker:
        raise UnsupportedPDF("unsupported PDF XMP structure")
    dom = minidom.parseString(source)

    def walk(node, inherited=""):
        local = (node.localName or "").lower()
        if local in {"thumbnails", "thumbnailimage"}:
            node.parentNode.removeChild(node)
            return
        role = inherited if local in {"li", "alt", "bag", "seq"} else local
        for i in range(node.attributes.length):
            attr = node.attributes.item(i)
            if attr.namespaceURI in {
                "http://www.w3.org/2000/xmlns/",
                "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "http://www.w3.org/XML/1998/namespace",
            }:
                continue
            attr.value = field(attr.localName or attr.name, attr.value, alias, redact)
        for child in tuple(node.childNodes):
            if child.nodeType == Node.ELEMENT_NODE:
                walk(child, role)
            elif (
                child.nodeType in {Node.TEXT_NODE, Node.CDATA_SECTION_NODE}
                and child.data.strip()
            ):
                child.data = field(role, child.data, alias, redact)

    walk(dom.documentElement)
    return dom.toxml()


def page_labels(doc, source_title, alias, redact):
    """Edit only /P strings in the native page-label number tree, including nested /Kids."""
    native = library().mupdf
    document = native.PdfDocument(doc.this)
    catalog = native.pdf_dict_gets(native.pdf_trailer(document), "Root")
    tree = [native.pdf_dict_gets(catalog, "PageLabels")]
    remaining = 150000
    visited = set()
    explicit = (
        len(source_title) >= 4
        and meaningful(source_title)
        and not structural(source_title)
    )
    while tree:
        entry = tree.pop()
        remaining -= 1
        if remaining < 0:
            raise UnsupportedPDF("oversized or cyclic PDF page-label tree")
        if not native.pdf_is_dict(entry):
            continue
        number = native.pdf_to_num(entry)
        if number:
            if number in visited:
                raise UnsupportedPDF("cyclic or duplicated PDF page-label tree")
            visited.add(number)
        children = native.pdf_dict_gets(entry, "Kids")
        size = native.pdf_array_len(children)
        if size > remaining:
            raise UnsupportedPDF("oversized PDF page-label tree")
        tree.extend(native.pdf_array_get(children, i) for i in range(size))
        numbers = native.pdf_dict_gets(entry, "Nums")
        size = native.pdf_array_len(numbers)
        if size % 2 or size > remaining * 2:
            raise UnsupportedPDF("invalid PDF page-label number array")
        remaining -= size // 2
        for i in range(1, size, 2):
            rule = native.pdf_array_get(numbers, i)
            prefix = native.pdf_dict_gets(rule, "P")
            if not native.pdf_is_string(prefix):
                continue
            value = native.pdf_to_text_string(prefix)
            if explicit and source_title in value:
                updated = alias(source_title).join(
                    redact(part) for part in value.split(source_title)
                )
            else:
                updated = redact(value)
            if updated != value:
                native.pdf_dict_puts(rule, "P", native.pdf_new_text_string(updated))


TYPED_STRINGS = (
    "Author",
    "Designer",
    "DrawnBy",
    "CheckedBy",
    "ApprovedBy",
    "LastSavedBy",
    "Company",
    "Organization",
    "Organisation",
    "Owner",
    "Project",
    "Title",
)


def metadata(doc, alias, redact, write):
    pdf = library()
    typ, reference = doc.xref_get_key(-1, "Info")
    entry = None
    standard = doc.metadata or {}
    if typ == "xref":
        entry = int(reference.split()[0])
        keys = {
            "Title": "title",
            "Author": "author",
            "Subject": "subject",
            "Keywords": "keywords",
            "Creator": "creator",
            "Producer": "producer",
        }
        for key in doc.xref_get_keys(entry):
            kind, value = doc.xref_get_key(entry, key)
            if kind == "xref" and key in keys:
                value = standard.get(keys[key]) or ""
            elif kind != "string":
                continue
            # The PDF Creator and Producer fields describe the software. XMP dc:creator
            # contains the actual author and is handled independently above.
            result = (
                redact(value)
                if key in {"Creator", "Producer"}
                else field(key, value, alias, redact)
            )
            if write and result != value:
                doc.xref_set_key(entry, key, pdf.get_pdf_str(result))
    for xref in range(1, doc.xref_length()):
        # PieceInfo stores application-private editing data. Illustrator and
        # prepress tools may include a complete extra copy of the artwork and
        # its source filename here. The PDF painting resources are separate.
        if write and doc.xref_get_key(xref, "PieceInfo")[0] != "null":
            doc.xref_set_key(xref, "PieceInfo", "null")
        if xref != entry:
            for key in TYPED_STRINGS:
                kind, value = doc.xref_get_key(xref, key)
                if kind == "string":
                    # Outline titles can be engineering navigation labels. A
                    # repeated Info title is typed; redact other labels in context.
                    changed = (
                        redact(value)
                        if key == "Title" and value != standard.get("title")
                        else field(key, value, alias, redact)
                    )
                    if write and changed != value:
                        doc.xref_set_key(xref, key, pdf.get_pdf_str(changed))
        if doc.xref_get_key(xref, "Type") == ("name", "/Metadata"):
            raw = doc.xref_stream(xref)
            if raw is None:
                raise UnsupportedPDF("PDF metadata dictionary has no readable stream")
            if len(raw) > 4 * 1024 * 1024:
                raise UnsupportedPDF("oversized PDF metadata")
            result = xmp(raw, alias, redact)
            if write:
                doc.update_stream(xref, result.encode("utf-8"))
        if doc.xref_get_key(xref, "S") == ("name", "/JavaScript") or doc.xref_get_key(
            xref, "Subtype"
        )[1] in {"/RichMedia", "/3D"}:
            raise UnsupportedPDF("PDF contains active scripts or embedded 3D media")
        if doc.xref_get_key(xref, "AF")[0] != "null":
            raise UnsupportedPDF("PDF associated-file relationship")
        # Alternate text and accessibility strings may repeat visible identifiers.
        for key in ("ActualText", "Alt", "TU"):
            kind, value = doc.xref_get_key(xref, key)
            if kind == "string" and write:
                changed = redact(value)
                if changed != value:
                    doc.xref_set_key(xref, key, pdf.get_pdf_str(changed))


def lines(page):
    pdf = library()
    result = page.get_text(
        "rawdict", flags=pdf.TEXTFLAGS_RAWDICT & ~pdf.TEXT_PRESERVE_IMAGES
    )
    for block in result["blocks"]:
        for line in block.get("lines", ()):
            spans = [(char, span) for span in line["spans"] for char in span["chars"]]
            text = "".join(char["c"] for char, _ in spans)
            if text and len(text) == len(spans):
                yield text, spans, line["dir"]


def title_fields(records):
    """Recognize an explicit author or client cell and a tightly aligned neighboring value."""
    pdf = library()
    result = {}
    boxes = [
        pdf.Rect(
            min(c["bbox"][0] for c, _ in chars),
            min(c["bbox"][1] for c, _ in chars),
            max(c["bbox"][2] for c, _ in chars),
            max(c["bbox"][3] for c, _ in chars),
        )
        for _, chars, _ in records
    ]
    for index, (text, _, direction) in enumerate(records):
        plain = text.strip().rstrip(":").casefold()
        if CORPORATE.fullmatch(text.strip()):
            result[index] = "organization"
        if plain not in PERSON_LABELS | COMPANY_LABELS or direction[0] < 0.98:
            continue
        label = boxes[index]
        choices = []
        for j, (candidate, _, angle) in enumerate(records):
            if j == index or angle[0] < 0.98:
                continue
            value = candidate.strip()
            words = (
                value.replace(".", " ")
                .replace("-", " ")
                .replace("’", " ")
                .replace("'", " ")
                .split()
            )
            if not (
                1 <= len(words) <= 4
                and 2 <= len(value) <= 54
                and all(w.isalpha() for w in words)
            ):
                continue
            if any(
                w.casefold() in TECHNICAL
                or w.casefold() in PERSON_LABELS | COMPANY_LABELS
                for w in words
            ):
                continue
            if len(words) == 1 and not (2 <= len(value) <= 4 and value.isupper()):
                continue
            box = boxes[j]
            h = max(label.height, box.height)
            dx, dy = box.x0 - label.x1, box.y0 - label.y0
            if -0.12 * h <= dx <= 4.4 * h and abs(dy) <= 0.38 * h:
                choices.append((abs(dx) / h + abs(dy) / h, j))
            elif abs(box.x0 - label.x0) < 0.6 * h and 0.65 * h < dy <= 1.7 * h:
                choices.append((1.4 + dy / h, j))
        choices.sort()
        if choices and (len(choices) == 1 or choices[1][0] - choices[0][0] > 0.6):
            result[choices[0][1]] = "person" if plain in PERSON_LABELS else "organization"
    return result


def matches(text, redactor, alias, strong=False, numeric_title=None):
    if strong:
        yield len(text) - len(text.lstrip()), len(text.rstrip()), pdf_identity.typed(strong, text.strip(), alias)
        return
    match = LABEL.match(text)
    if match and meaningful(match["value"]):
        start, end = match.span("value")
        role = "person" if match["label"].casefold() in PERSON_LABELS else ""
        yield start, end, pdf_identity.typed(role, match["value"], alias)
    else:
        general = list(redactor.matches(text))
        if numeric_title:
            # A numeric drawing number can also occur as a dimension on an
            # unrelated drawing. Restrict this typed title to its own document.
            for match in numeric_title.finditer(text):
                if not any(
                    a < match.end() and b > match.start() for a, b, _ in general
                ):
                    general.append((match.start(), match.end(), alias(match[0])))
        yield from sorted(general)


def redact_page(page, alias, redactor, numeric_title):
    pdf = library()
    drawings = []
    records = list(lines(page))
    strong = title_fields(records)
    for i, (text, spans, direction) in enumerate(records):
        for start, stop, replacement in matches(
            text, redactor, alias, strong.get(i), numeric_title
        ):
            selected = spans[start:stop]
            char, style = selected[0]
            dx, dy = direction
            origin = pdf.Point(char["origin"])
            # Only the central portion of each glyph is needed to erase it. A full
            # line box can overlap dimension text on the row above or below.
            end = origin
            for glyph, _ in selected:
                rect = pdf.Rect(glyph["bbox"])
                if rect.is_empty or rect.is_infinite:
                    continue
                margin_x, margin_y = rect.width * 0.4, rect.height * 0.4
                page.add_redact_annot(
                    rect + (margin_x, margin_y, -margin_x, -margin_y),
                    fill=False,
                    cross_out=False,
                )
                end = pdf.Point(glyph["origin"])
            last = pdf.Rect(selected[-1][0]["bbox"])
            length = max(
                1,
                (end.x - origin.x) * dx
                + (end.y - origin.y) * dy
                + (last.width if abs(dx) >= abs(dy) else last.height),
            )
            size = max(1, style["size"])
            width = pdf.get_text_length(replacement, fontname="helv", fontsize=size)
            scale = min(1.0, length / max(1, width))
            matrix = pdf.Matrix(scale * dx, -scale * dy, dy, dx, 0, 0)
            drawings.append(
                (origin, replacement, size, pdf.sRGB_to_pdf(style["color"]), matrix)
            )
    if drawings:
        page.apply_redactions(
            images=pdf.PDF_REDACT_IMAGE_NONE, graphics=pdf.PDF_REDACT_LINE_ART_NONE
        )
        for origin, replacement, size, color, matrix in drawings:
            page.insert_text(
                origin, replacement, fontsize=size, color=color, morph=(origin, matrix)
            )
    if page.parent.xref_get_key(page.xref, "Thumb")[0] != "null":
        page.parent.xref_set_key(page.xref, "Thumb", "null")


def member(name, payload, alias, redact, reference, write):
    handler = MEMBERS.get(PurePosixPath(name.replace("\\", "/")).suffix.lower())
    if handler is None or len(payload) > ATTACH_MAX:
        raise UnsupportedPDF("unsupported or oversized PDF attachment")
    if not write:
        stem = PurePosixPath(name.replace("\\", "/")).stem
        if not structural(stem):
            alias(stem)
    for value in handler.discover(payload):
        if not write:
            alias(value)
    filename = reference(name) if write else name
    new = handler.rewrite(payload, alias, redact, reference) if write else payload
    return filename, new


def replace_attachment(doc, annot, filename, payload, description):
    """Rewrite the existing file specification and stream, keeping its annotation appearance."""
    pdf = library()
    kind, address = doc.xref_get_key(annot.xref, "FS")
    if kind == "xref":
        specification, prefix = int(address.split()[0]), ""
    elif kind == "dict":
        specification, prefix = annot.xref, "FS/"
    else:
        raise UnsupportedPDF("missing PDF attachment specification")
    resources = set()
    for key in ("F", "UF", "DOS", "Mac", "Unix"):
        kind, ref = doc.xref_get_key(specification, prefix + "EF/" + key)
        if kind == "xref":
            resources.add(int(ref.split()[0]))
        if (
            key in {"F", "UF"}
            or doc.xref_get_key(specification, prefix + key)[0] != "null"
        ):
            doc.xref_set_key(specification, prefix + key, pdf.get_pdf_str(filename))
    if not resources:
        raise UnsupportedPDF("missing PDF attachment stream")
    doc.xref_set_key(specification, prefix + "Desc", pdf.get_pdf_str(description))
    for xref in resources:
        doc.update_stream(xref, payload)
        doc.xref_set_key(xref, "DL", str(len(payload)))
        doc.xref_set_key(xref, "Params/Size", str(len(payload)))
        if doc.xref_get_key(xref, "Params/CheckSum")[0] != "null":
            doc.xref_set_key(
                xref, "Params/CheckSum", "<" + hashlib.md5(payload).hexdigest() + ">"
            )
        doc.xref_set_key(xref, "Type", "/EmbeddedFile")


def process(data, alias, redact, reference, write):
    with document(data) as doc:
        title = ((doc.metadata or {}).get("title") or "").strip()
        numeric_title = (
            re.compile(
                r"(?<![\w.])(?<![0-9],)"
                + re.escape(title)
                + r"(?![\w.]|,[0-9]|\s*(?:[×x]\s*[0-9]|[°\x22\x27]|"
                r"(?:mm|cm|[µu]m|m|inch(?:es)?|in|ft|kg|g|deg(?:rees)?|kN|Nm|rpm|mpa|psi)\b))",
                re.I,
            )
            if re.fullmatch(r"[0-9]{5,}", title)
            else None
        )
        metadata(doc, alias, redact, write)
        if write:
            page_labels(doc, title, alias, redact)
        attachments = []
        processed_payloads = set()
        for name in doc.embfile_names():
            info = doc.embfile_info(name)
            if info["size"] > ATTACH_MAX:
                raise UnsupportedPDF("oversized PDF attachment")
            attachments.append((name, info))
        total = sum(info["size"] for _, info in attachments)
        if total > ATTACH_MAX:
            raise UnsupportedPDF("oversized set of PDF attachments")
        renamed = set()
        for name, info in attachments:
            original = info.get("ufilename") or info.get("filename") or name
            filename, payload = member(
                original, doc.embfile_get(name), alias, redact, reference, write
            )
            if write:
                if filename in renamed or (
                    filename != name and filename in doc.embfile_names()
                ):
                    raise UnsupportedPDF("ambiguous PDF attachment name")
                renamed.add(filename)
                processed_payloads.add(hashlib.sha256(payload).digest())
                doc.embfile_del(name)
                entry = doc.embfile_add(
                    filename,
                    payload,
                    filename=filename,
                    ufilename=filename,
                    desc=redact(info.get("description", "")),
                )
                # embfile_add writes the current clock into both values. Restore the
                # source timestamps (or their absence) for deterministic reruns.
                for key, tag in (
                    ("creationDate", "CreationDate"),
                    ("modDate", "ModDate"),
                ):
                    original_date = info.get(key)
                    doc.xref_set_key(
                        entry,
                        "Params/" + tag,
                        library().get_pdf_str(original_date)
                        if original_date
                        else "null",
                    )

        for page in doc:
            for annot in page.annots() or ():
                if annot.type[1] == "Redact":
                    raise UnsupportedPDF("preexisting pending PDF redaction")
                info = annot.info
                if doc.xref_get_key(annot.xref, "RC")[0] != "null":
                    raise UnsupportedPDF("PDF contains rich-text annotation content")
                if info:
                    for key, tag in (
                        ("title", "T"),
                        ("subject", "Subj"),
                        ("content", "Contents"),
                    ):
                        value = info.get(key, "")
                        if not value:
                            continue
                        result = pdf_identity.typed("person", value, alias) if key == "title" else redact(value)
                        if write and result != value:
                            if annot.type[1] == "FreeText" and key == "content":
                                raise UnsupportedPDF(
                                    "PDF free-text annotation requires appearance regeneration"
                                )
                            doc.xref_set_key(
                                annot.xref, tag, library().get_pdf_str(result)
                            )
                if annot.type[1] == "FileAttachment":
                    details = annot.file_info
                    total += details["size"]
                    if total > ATTACH_MAX:
                        raise UnsupportedPDF("oversized set of PDF attachments")
                    filename, contents = member(
                        details.get("ufilename") or details["filename"],
                        annot.get_file(),
                        alias,
                        redact,
                        reference,
                        write,
                    )
                    if write:
                        processed_payloads.add(hashlib.sha256(contents).digest())
                        replace_attachment(
                            doc,
                            annot,
                            filename,
                            contents,
                            redact(details.get("description", "")),
                        )
            if write:
                for link in page.get_links():
                    key = (
                        "uri"
                        if link.get("uri")
                        else "file"
                        if link.get("file")
                        else None
                    )
                    if key:
                        result = (
                            redact(link[key]) if key == "uri" else reference(link[key])
                        )
                        if result != link[key]:
                            link[key] = result
                            page.update_link(link)
                redact_page(page, alias, redact, numeric_title)
            else:
                records = list(lines(page))
                strong = title_fields(records)
                for i, (text, _, _) in enumerate(records):
                    if i in strong:
                        pdf_identity.typed(strong[i], text.strip(), alias)
                    match = LABEL.match(text)
                    if match and meaningful(match["value"]):
                        role = "person" if match["label"].casefold() in PERSON_LABELS else ""
                        pdf_identity.typed(role, match["value"], alias)
        if write:
            for i, (_, title, _) in enumerate(doc.get_toc()):
                changed = redact(title)
                if changed != title:
                    doc.set_toc_item(i, title=changed)
            result = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
            with document(result) as saved:
                for entry in range(1, saved.xref_length()):
                    if saved.xref_get_key(entry, "Type") == ("name", "/EmbeddedFile"):
                        if (
                            hashlib.sha256(saved.xref_stream(entry)).digest()
                            not in processed_payloads
                        ):
                            raise UnsupportedPDF("untracked PDF embedded file")
            return result


def discover(data):
    values = pdf_identity.Observations()

    def add(value):
        if meaningful(value):
            values.add(value)
        return value

    add.observe = values.observe
    process(data, add, lambda value: value, lambda value: value, False)
    return values.finish()


def rewrite(data, alias, redact_text, rewrite_path):
    return process(data, alias, redact_text, rewrite_path, True)
