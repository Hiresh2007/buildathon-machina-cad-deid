"""Independently verify private PDF renders and STL meshes against their source files."""

import argparse
import collections
from contextlib import closing
import io
import logging
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


UNITS = re.compile(
    r"(?<![0-9a-z])[0-9]+(?:[.,][0-9]+)?\s*(?:mm|inch(?:es)?|cm|kg|mpa|deg(?:rees)?|kN|Nm|psi|ft|rpm)\b",
    re.I,
)


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


def native_page(a, b, counts):
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
        if a_pixels.shape != b_pixels.shape:
            raise ValueError("native render dimensions differ")
        height, width = a_pixels.shape[:2]
        for left, right in zip(original, result):
            if np.allclose(left, right, rtol=0, atol=0.002):
                continue
            x0 = max(0, int(min(left[0], right[0]) * scale) - 2)
            x1 = min(width, int(max(left[2], right[2]) * scale) + 3)
            y0 = max(0, height - int(max(left[3], right[3]) * scale) - 2)
            y1 = min(height, height - int(min(left[1], right[1]) * scale) + 3)
            if not np.array_equal(a_pixels[y0:y1, x0:x1], b_pixels[y0:y1, x0:x1]):
                raise ValueError("changed vector bounds produce different pixels")
    with closing(a.get_textpage()) as at, closing(b.get_textpage()) as bt:
        units = collections.Counter(UNITS.findall(at.get_text_bounded()))
        if units - collections.Counter(UNITS.findall(bt.get_text_bounded())):
            raise ValueError("dimension or unit text is missing")
    counts["measured dimensions"] += sum(units.values())
    counts["vector objects"] += len(original)
    counts["image objects"] += images
    counts["PDF pages"] += 1


def check_pdf(before, after, ids, counts):
    pypdf.PdfReader(io.BytesIO(after))
    try:
        pypdf.PdfReader(io.BytesIO(before))
    except pypdf.errors.PdfReadError:
        counts["source files rejected by pypdf"] += 1
    with (
        closing(pdfium.PdfDocument(before)) as a,
        closing(pdfium.PdfDocument(after)) as b,
    ):
        if len(a) != len(b):
            raise ValueError("page count differs")
        for left, right in zip(a, b):
            native_page(left, right, counts)
            left.close()
            right.close()
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
                if len(value) >= 4:
                    originals.add(value)
        text = "\n".join(p.get_text() for p in b)
        if any(value in text for value in originals):
            raise ValueError("source metadata value appears in page text")
        probes = [
            pattern
            for value in originals
            for pattern in (value.encode(), value.encode("utf-16be"))
        ]
        for i in range(1, b.xref_length()):
            data = b.xref_object(i).encode() + (b.xref_stream(i) or b"")
            if any(pattern in data for pattern in probes):
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
