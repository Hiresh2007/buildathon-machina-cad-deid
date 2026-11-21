"""Regression checks for independent PDF drawing and privacy verification."""

import collections
import unittest
from contextlib import closing

import numpy as np
import pypdfium2 as pdfium
import pymupdf

from check_native_formats import (
    preserves_measurement_spacing,
    check_pdf,
    check_identities,
    graphics_render,
    hide_text_paint,
    measurements,
    recover_path_precision,
    verified_pdf,
)
from pipeline import Identities
from drawing_pdf import discover, rewrite, UnsupportedPDF


def drawing(changed_geometry=False, caption="test caption"):
    with pymupdf.open() as doc:
        page = doc.new_page(width=120, height=120)
        points = [(15, 85), (60, 110), (105, 85)]
        if changed_geometry:
            points[1] = (85, 110)
        page.draw_polyline(points)
        page.insert_text((12, 35), caption)
        return doc.tobytes()


class PDFFidelity(unittest.TestCase):
    def test_extracted_unit_spacing_requires_unchanged_physical_glyphs(self):
        def page_with(label, x):
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((x, 30), label)
                page.insert_text((x, 60), label)
                return document.tobytes()

        before = collections.Counter({"25 mm": 2})
        changed_space = collections.Counter({"25mm": 2})
        original = page_with("25 mm", 10)
        for payload, expected in [(original, True), (page_with("25 mm", 10.02), False),
                                  (page_with("26 mm", 10), False), (page_with("25 cm", 10), False)]:
            with (closing(pdfium.PdfDocument(original)) as first, closing(pdfium.PdfDocument(payload)) as second,
                  closing(first[0]) as a, closing(second[0]) as b,
                  closing(a.get_textpage()) as left, closing(b.get_textpage()) as right):
                self.assertEqual(preserves_measurement_spacing(left, right, before, changed_space), expected)
                self.assertFalse(preserves_measurement_spacing(left, right, before, collections.Counter({"25mm": 1})))

    def test_image_with_pdf_extension(self):
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
        image.clear_with(255)
        with self.assertRaises(UnsupportedPDF):
            discover(image.tobytes("png"))

    def test_metadata_dictionary_without_stream(self):
        with pymupdf.open(stream=drawing()) as doc:
            metadata = doc.get_new_xref()
            doc.update_object(metadata, "<< /Type /Metadata /Subtype /XML >>")
            doc.xref_set_key(doc.pdf_catalog(), "Metadata", f"{metadata} 0 R")
            source = doc.tobytes()
        with self.assertRaisesRegex(UnsupportedPDF, "no readable stream"):
            discover(source)

    def test_empty_associated_file_array(self):
        with pymupdf.open(stream=drawing(caption="Depth 12mm")) as doc:
            doc.set_metadata({"author": "Fixture Owner"})
            doc.xref_set_key(doc.pdf_catalog(), "AF", "[  ]")
            source = doc.tobytes()
            attachment = doc.get_new_xref()
            doc.update_object(attachment, "<< /Type /Filespec /F (sample.step) >>")
            doc.xref_set_key(doc.pdf_catalog(), "AF", f"[{attachment} 0 R]")
            associated = doc.tobytes()

        identities = Identities(bytes(32))
        for value in discover(source):
            identities.learn(value)
        output = rewrite(source, identities.alias, identities, lambda path: path)
        check_pdf(source, output, identities, collections.Counter())
        with pymupdf.open(stream=output) as doc:
            self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), "AF"), ("array", "[]"))
        with self.assertRaisesRegex(UnsupportedPDF, "associated-file relationship"):
            discover(associated)

    @staticmethod
    def path_fixture(caption, coordinate, frame="", last=b"l"):
        with pymupdf.open() as doc:
            for _ in range(2):
                page = doc.new_page(width=120, height=120)
                page.insert_text((10, 25), caption)
                page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(5, 10, 90, 30),
                                  "uri": "https://fixture.invalid/drawing"})
                xref = doc.get_new_xref()
                doc.update_object(xref, "<<>>")
                geometry = f"q {frame} 10 10 m 35.12345 40 l {coordinate} 65 ".encode()
                doc.update_stream(xref, geometry + last + b" S Q")
                streams = [*page.get_contents(), xref]
                doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{r} 0 R" for r in streams) + "]")
            return doc.tobytes()

    def test_verified_path_precision_keeps_sanitized_text_and_links(self):
        source = self.path_fixture("PRIVATE OWNER", "55.12345")
        cleaned = self.path_fixture("Anon fixture", "55.12")
        ids = Identities(bytes(32))
        with self.assertRaisesRegex(ValueError, "vector commands or styles differ"):
            check_pdf(source, cleaned, ids, collections.Counter())
        counts = collections.Counter()
        result = verified_pdf(source, cleaned, ids, counts)
        self.assertEqual(counts["PDFs recovered from path rounding"], 1)
        self.assertEqual(counts["PDF pages"], 2)
        with pymupdf.open(stream=result) as restored:
            for page in restored:
                self.assertEqual(page.get_text().strip(), "Anon fixture")
                self.assertEqual(page.get_links()[0]["uri"], "https://fixture.invalid/drawing")
        self.assertEqual(verified_pdf(source, result, ids, collections.Counter()), result)

    def test_precision_requires_matching_paths_frames_and_small_changes(self):
        source = self.path_fixture("PRIVATE OWNER", "55.12345")
        for candidate in (self.path_fixture("Anon fixture", "53"),
                          self.path_fixture("Anon fixture", "55.12", "1 0 0 1 1 0 cm"),
                          self.path_fixture("Anon fixture", "55.12", last=b"m")):
            self.assertIsNone(recover_path_precision(source, candidate))

    def test_geometry_plane(self):
        with closing(pdfium.PdfDocument(drawing())) as doc, closing(doc[0]) as page:
            source = graphics_render(page, 120, 120)
            hide_text_paint(page)
            geometry = graphics_render(page, 120, 120)
            self.assertFalse(np.array_equal(source[:60], geometry[:60]))
            self.assertTrue(np.all(geometry[:60] == 255))
            self.assertTrue(np.array_equal(source[60:], geometry[60:]))
            self.assertTrue(np.any(geometry[60:] < 250))

    def test_changed_shape_with_identical_bounds(self):
        with self.assertRaisesRegex(ValueError, "vector commands or styles differ"):
            check_pdf(
                drawing(), drawing(True), Identities(bytes(32)), collections.Counter()
            )

    def test_structural_names_and_pdf_string_contents(self):
        identities = Identities(bytes(32))
        with pymupdf.open(stream=drawing()) as doc:
            doc.set_metadata({"title": "Page"})
            source = doc.tobytes()
            doc.set_metadata({"title": identities.alias("Page")})
            check_identities(source, doc.tobytes(), identities, collections.Counter())
            doc.xref_set_key(
                doc.pdf_catalog(), "ApplicationTitle", "<FEFF0050006100670065>"
            )
            with self.assertRaisesRegex(ValueError, "original metadata value"):
                check_identities(
                    source, doc.tobytes(), identities, collections.Counter()
                )

    def test_measurement_outside_url(self):
        self.assertEqual(
            measurements("See https://12mm.example.invalid/drawings. Bore 12mm."),
            {"12mm": 1},
        )
        with self.assertRaisesRegex(ValueError, "dimension or unit text is missing"):
            check_pdf(
                drawing(caption="Depth 12mm"),
                drawing(),
                Identities(bytes(32)),
                collections.Counter(),
            )


if __name__ == "__main__":
    unittest.main()
