"""Independently verify private PDF renders and STL meshes against their source files."""

import argparse
import collections
from contextlib import closing
import io
import logging
import math
import re
from pathlib import Path

import numpy as np
import pypdf
import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pymupdf
import trimesh

from drawing_pdf import meaningful
from naming import structural
from pipeline import Identities, PathIndex
import pdf_identity


UNITS = re.compile(
    r"(?<![0-9a-z])[0-9]+(?:[.,][0-9]+)?\s*(?:mm|inch(?:es)?|cm|kg|mpa|deg(?:rees)?|kN|Nm|psi|ft|rpm)\b",
    re.I,
)


WEB_ADDRESS = re.compile(r"(?:https?://|www\.)[^\s<>\x22']+", re.I)


def measurement_matches(text):
    addresses = [match.span() for match in WEB_ADDRESS.finditer(text)]
    return (match for match in UNITS.finditer(text)
            if not any(left <= match.start() and match.end() <= right for left, right in addresses))


def measurements(text):
    return collections.Counter(match[0] for match in measurement_matches(text))


def compact_units(values):
    result = collections.Counter()
    for spelling, count in values.items():
        result["".join(spelling.split())] += count
    return result


def measurement_boxes(page_text, needed):
    # PDFium can synthesize or omit a space while extracting the same glyphs.
    # Its indexed Unicode text gives a physical box for each original character.
    length = page_text.count_chars()
    if length > 250000:
        return {}
    text = "".join(chr(raw.FPDFText_GetUnicode(page_text, i)) for i in range(length))
    found = collections.defaultdict(list)
    for match in measurement_matches(text):
        label = "".join(match[0].split())
        if label in needed:
            boxes = tuple(page_text.get_charbox(i) for i in range(*match.span()) if not text[i].isspace())
            found[label].append(boxes)
    return found


def preserves_measurement_spacing(original, edited, before, after):
    required = compact_units(before - after)
    if not required:
        return True
    source_counts, target_counts = compact_units(before), compact_units(after)
    if any(source_counts[label] != target_counts[label] for label in required):
        return False
    first = measurement_boxes(original, required)
    second = measurement_boxes(edited, required)
    for label in required:
        left, right = sorted(first.get(label, ())), sorted(second.get(label, ()))
        if len(left) != source_counts[label] or len(left) != len(right):
            return False
        for a, b in zip(left, right):
            # The remaining characters encode the exact digits, decimal mark,
            # and case-sensitive unit. Compare every physical bound in points.
            if len(a) != len(b) or any(not math.isclose(x, y, rel_tol=0, abs_tol=0.0001)
                                       for ga, gb in zip(a, b) for x, y in zip(ga, gb)):
                return False
    return True


def check_mesh(before, after, counts):
    def read(data):
        return trimesh.load(
            io.BytesIO(data), file_type="stl", process=False, force="mesh"
        )

    a, b = read(before), read(after)
    for field in ("vertices", "faces", "face_normals"):
        if not np.array_equal(getattr(a, field), getattr(b, field)):
            raise ValueError("STL mesh differs")
    counts["STL facets"] += len(a.faces)


def same_paint(left, right):
    """Compare ordered native painting commands, styles, clipping and image placement."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            key == "seqno" or same_paint(left[key], right[key]) for key in left
        )
    shapes = (tuple, list, pymupdf.Point, pymupdf.Rect, pymupdf.Quad)
    if isinstance(left, shapes) and isinstance(right, shapes):
        return len(left) == len(right) and all(
            same_paint(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, float) and isinstance(right, float):
        return math.isclose(left, right, rel_tol=0, abs_tol=0.0001)
    return left == right


def hide_text_paint(page):
    """Render the full nontext plane, retaining any text clipping of later artwork."""
    for obj in page.get_objects(max_depth=32):
        if obj.type == raw.FPDF_PAGEOBJ_FORM and obj.level == 31:
            raise ValueError("PDF graphics exceed supported form depth")
        if obj.type == raw.FPDF_PAGEOBJ_TEXT:
            mode = raw.FPDFTextObj_GetTextRenderMode(obj)
            if not 0 <= mode <= 7 or not raw.FPDFTextObj_SetTextRenderMode(
                obj,
                raw.FPDF_TEXTRENDERMODE_CLIP
                if mode >= 4
                else raw.FPDF_TEXTRENDERMODE_INVISIBLE,
            ):
                raise ValueError("unable to isolate the PDF graphics plane")


def graphics_render(page, width, height, flags=0):
    with closing(
        pdfium.PdfBitmap.new_native(width, height, format=raw.FPDFBitmap_BGR)
    ) as bitmap:
        bitmap.fill_rect(0, 0, width, height, (255, 255, 255, 255))
        raw.FPDF_RenderPageBitmap(bitmap, page, 0, 0, width, height, 0, flags)
        return bitmap.to_numpy().copy()


def verify_paint(mupa, mupb):
    if not same_paint(
        mupa.get_drawings(extended=True), mupb.get_drawings(extended=True)
    ):
        raise ValueError("vector commands or styles differ")

    def image_signature(page):
        return [
            {
                key: image[key]
                for key in (
                    "width",
                    "height",
                    "bpc",
                    "colorspace",
                    "digest",
                    "transform",
                )
            }
            for image in page.get_image_info(hashes=True)
        ]

    if not same_paint(image_signature(mupa), image_signature(mupb)):
        raise ValueError("image pixels or placements differ")


def verify_graphics_render(a, b, scale):
    hide_text_paint(a)
    hide_text_paint(b)
    width, height = [math.ceil(n * scale) for n in a.get_size()]
    before = graphics_render(a, width, height)
    after = graphics_render(b, width, height)
    # Even equivalent floating-point paint commands can round an antialiased
    # edge differently. Bound every channel, on every pixel of the full page.
    if any(
        np.abs(before[row : row + 128].astype(np.int16) - after[row : row + 128]).max()
        > 2
        for row in range(0, height, 128)
    ):
        # A sub-pixel edge may cross a rounding boundary in antialiasing.
        # Require a second, exactly equal full-page raster without path smoothing.
        if not np.array_equal(
            graphics_render(a, width, height, raw.FPDF_RENDER_NO_SMOOTHPATH),
            graphics_render(b, width, height, raw.FPDF_RENDER_NO_SMOOTHPATH),
        ):
            raise ValueError("independent graphics-only renders differ")


def native_page(a, b, mupa, mupb, counts):
    if not np.allclose(a.get_size(), b.get_size(), rtol=0, atol=0.001):
        raise ValueError("page dimensions differ")

    def objects(page, kind):
        return [obj.get_pos() for obj in page.get_objects(filter=(kind,))]

    original, result = (
        objects(a, raw.FPDF_PAGEOBJ_PATH),
        objects(b, raw.FPDF_PAGEOBJ_PATH),
    )
    if len(original) != len(result):
        raise ValueError("vector object count differs")
    images = len(objects(a, raw.FPDF_PAGEOBJ_IMAGE))
    if len(objects(b, raw.FPDF_PAGEOBJ_IMAGE)) != images:
        raise ValueError("image object count differs")
    # Native writers can normalize curve bounding boxes. Render each disputed
    # region independently in PDFium before reporting a geometric difference.
    scale = min(2, 2800 / max(a.get_size()))
    with (
        closing(a.render(scale=scale)) as a_bmp,
        closing(b.render(scale=scale)) as b_bmp,
    ):
        a_pixels, b_pixels = a_bmp.to_numpy(), b_bmp.to_numpy()
        verify_graphics = a_pixels.shape != b_pixels.shape
        height, width = a_pixels.shape[:2]
        for left, right in zip(original, result):
            if verify_graphics:
                break
            if np.allclose(left, right, rtol=0, atol=0.002):
                continue
            x0 = max(0, int(min(left[0], right[0]) * scale) - 2)
            x1 = min(width, int(max(left[2], right[2]) * scale) + 3)
            y0 = max(0, height - int(max(left[3], right[3]) * scale) - 2)
            y1 = min(height, height - int(min(left[1], right[1]) * scale) + 3)
            if not np.array_equal(a_pixels[y0:y1, x0:x1], b_pixels[y0:y1, x0:x1]):
                verify_graphics = True
                break
    with closing(a.get_textpage()) as at, closing(b.get_textpage()) as bt:
        units = measurements(at.get_text_bounded())
        if not preserves_measurement_spacing(at, bt, units, measurements(bt.get_text_bounded())):
            raise ValueError("dimension or unit text is missing")
    verify_paint(mupa, mupb)
    if verify_graphics:
        verify_graphics_render(a, b, scale)
        counts["pages independently verified without text paint"] += 1
    counts["measured dimensions"] += sum(units.values())
    counts["vector objects"] += len(original)
    counts["image objects"] += images
    counts["PDF pages"] += 1


def check_pdf(before, after, ids, counts):
    try:
        pypdf.PdfReader(io.BytesIO(before))
    except pypdf.errors.PdfReadError:
        counts["source files rejected by pypdf"] += 1
    with (
        closing(pdfium.PdfDocument(before)) as a,
        closing(pdfium.PdfDocument(after)) as b,
        pymupdf.open(stream=before) as ma,
        pymupdf.open(stream=after) as mb,
    ):
        if len(a) != len(b):
            raise ValueError("page count differs")
        for left, right, mupa, mupb in zip(a, b, ma, mb):
            native_page(left, right, mupa, mupb, counts)
            left.close()
            right.close()
    check_identities(before, after, ids, counts)


# PDF path construction and paint operators. Their operands are numbers only.
PATH_COORDINATES = {b"m": 2, b"l": 2, b"c": 6, b"v": 4, b"y": 4, b"re": 4}
PATH_END = set(b"h W W* n s S f f* F b b* B B*".split())
PDF_IDENTITY_MATRIX = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def path_operations(content):
    matrix, stack = PDF_IDENTITY_MATRIX, []
    for i, (args, operator) in enumerate(content.operations):
        if operator == b"q":
            stack.append(matrix)
        elif operator == b"Q":
            if not stack:
                raise ValueError("unbalanced PDF graphics state")
            matrix = stack.pop()
        elif operator == b"cm":
            a, b, c, d, e, f = map(float, args)
            xx, xy, yx, yy, dx, dy = matrix
            matrix = (a * xx + b * yx, a * xy + b * yy,
                      c * xx + d * yx, c * xy + d * yy,
                      e * xx + f * yx + dx, e * xy + f * yy + dy)
            if not all(map(math.isfinite, matrix)):
                raise ValueError("non-finite PDF transformation")
        elif operator in PATH_COORDINATES or operator in PATH_END:
            if len(args) != PATH_COORDINATES.get(operator, 0):
                raise ValueError("unexpected number of PDF coordinates")
            yield i, operator, matrix, args
    if stack:
        raise ValueError("unbalanced PDF graphics state")


def precise_path_stream(source, target):
    # Retain the sanitized page's instructions and resources. Transfer only source
    # coordinates when the complete path sequence and its coordinate frames match.
    numbers = (pypdf.generic.NumberObject, pypdf.generic.FloatObject)
    original = list(path_operations(source))
    cleaned = list(path_operations(target))
    if len(original) != len(cleaned):
        return None
    operations = list(target.operations)
    changed = False
    for (_, op, frame, before), (i, current, other_frame, after) in zip(original, cleaned):
        if op != current or any(abs(a - b) > 1e-9 for a, b in zip(frame, other_frame)):
            return None
        if not all(isinstance(n, numbers) and math.isfinite(float(n)) for n in (*before, *after)):
            return None
        if any(abs(float(a) - float(b)) > 0.06 for a, b in zip(before, after)):
            return None
        if before != after:
            operations[i] = (before, op)
            changed = True
    if changed:
        target.operations = operations
        return target.get_data()
    return None


def text_and_links(doc):
    result = []
    for page in doc:
        links = [{k: v for k, v in link.items() if k not in ("xref", "id")}
                 for link in page.get_links()]
        result.append((page.get_text(), links))
    return result


def recover_path_precision(before, after):
    source = pypdf.PdfReader(io.BytesIO(before), strict=False)
    cleaned = pypdf.PdfReader(io.BytesIO(after), strict=False)
    if len(source.pages) != len(cleaned.pages):
        return None
    changed = False
    with pymupdf.open(stream=after) as result:
        preserved = text_and_links(result)
        for i, (original_page, cleaned_page) in enumerate(zip(source.pages, cleaned.pages)):
            if "/Contents" not in original_page or "/Contents" not in cleaned_page:
                continue
            candidate = precise_path_stream(
                pypdf.generic.ContentStream(original_page["/Contents"], source),
                pypdf.generic.ContentStream(cleaned_page["/Contents"], cleaned),
            )
            if candidate is None:
                continue
            xref = result.get_new_xref()
            result.update_object(xref, "<<>>")
            result.update_stream(xref, candidate)
            result[i].set_contents(xref)
            changed = True
        if not changed:
            return None
        candidate = result.tobytes(garbage=4, deflate=True, no_new_id=True)
    with pymupdf.open(stream=candidate) as reread:
        if text_and_links(reread) != preserved:
            return None
    return candidate


def verified_pdf(before, after, ids, counts):
    measured = collections.Counter()
    try:
        check_pdf(before, after, ids, measured)
    except ValueError as error:
        if str(error) not in {"independent graphics-only renders differ", "vector commands or styles differ",
                               "vector object count differs"}:
            raise
        repaired = recover_path_precision(before, after)
        if repaired is None:
            raise
        measured.clear()
        check_pdf(before, repaired, ids, measured)
        measured["PDFs recovered from path rounding"] += 1
        after = repaired
    counts.update(measured)
    return after


def contains_original(value, originals, probes, depth=0):
    if depth > 80:
        raise ValueError("unsupported PDF object nesting")
    if isinstance(value, pypdf.generic.IndirectObject):
        return False  # Every indirect object is visited independently.
    if isinstance(value, dict):
        for key, item in value.items():
            # The page tree names are compulsory PDF structure. An author or
            # title can coincidentally be a substring of these exact names.
            if (
                key == "/Pages"
                and value.get("/Type") == "/Catalog"
                and isinstance(item, pypdf.generic.IndirectObject)
            ):
                continue
            if (
                key == "/Type"
                and isinstance(item, pypdf.generic.NameObject)
                and item in ("/Catalog", "/Pages", "/Page")
            ):
                continue
            if contains_original(
                key, originals, probes, depth + 1
            ) or contains_original(item, originals, probes, depth + 1):
                return True
        return False
    if isinstance(value, list):
        return any(
            contains_original(item, originals, probes, depth + 1) for item in value
        )
    if isinstance(value, bytes):
        return any(pattern in value for pattern in probes)
    if isinstance(value, pypdf.generic.TextStringObject):
        original_bytes = value.original_bytes
        if any(pattern in original_bytes for pattern in probes):
            return True
    return any(text in str(value) for text in originals)


def check_identities(before, after, ids, counts):
    reader = pypdf.PdfReader(io.BytesIO(after))
    with pymupdf.open(stream=before) as a, pymupdf.open(stream=after) as b:
        originals = set()
        for key in ("author", "title"):
            value = a.metadata.get(key)
            if (
                value
                and meaningful(value)
                and not (key == "title" and structural(value))
            ):
                if b.metadata.get(key) != ids.alias(value):
                    raise ValueError("metadata value does not match its pseudonym")
                neutral_filename = key == "title" and pdf_identity.asset(value) and (
                    pdf_identity.asset_title(value, lambda part: "\0", ids.preserve_directories, ids.link_repositories) == value
                )
                if len(value) >= 4 and not neutral_filename:
                    originals.add(value)
        text = "\n".join(p.get_text() for p in b)
        if any(value in text for value in originals):
            raise ValueError("source metadata value appears in page text")
        probes = [
            pattern
            for value in originals
            for pattern in (
                value.encode("utf-8", "surrogatepass"),
                value.encode("utf-16be", "surrogatepass"),
                value.encode("utf-16le", "surrogatepass"),
            )
        ]
        if originals:
            for i in range(1, b.xref_length()):
                stream = b.xref_stream(i) or b""
                if any(pattern in stream for pattern in probes) or contains_original(
                    reader.get_object(i), originals, probes
                ):
                    raise ValueError(
                        "original metadata value appears in an output object or decoded stream"
                    )
        counts["metadata identities scanned"] += len(originals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "attempt", "key-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    ids = Identities(args.key_file.read_bytes())
    paths = [
        p.relative_to(args.input)
        for p in args.input.rglob("*")
        if p.is_file() or p.is_symlink()
    ]
    pdf_identity.restore_directory_forms(args.input, args.attempt, paths, ids)
    index = PathIndex(paths, ids)
    counts, defects = collections.Counter(), []
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    for path in sorted(paths):
        ext = path.suffix.lower()
        if ext not in {".stl", ".pdf"}:
            continue
        final = index.targets[path.as_posix()]
        target = args.attempt / "corpus" / final
        if not target.is_file():
            counts["omitted from attempt"] += 1
            continue
        try:
            before, after = (args.input / path).read_bytes(), target.read_bytes()
            if ext == ".stl":
                check_mesh(before, after, counts)
            else:
                check_pdf(before, after, ids, counts)
            counts[ext] += 1
        except Exception as error:
            defects.append((final, type(error).__name__))
    print(dict(sorted(counts.items())))
    for path, kind in defects:
        print(f"Verification exception in {path} ({kind})")
    if defects:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
