"""Regression checks for independent PDF drawing and privacy verification."""

import collections
import unittest
from contextlib import closing

import numpy as np
import pypdfium2 as pdfium
import pymupdf

from check_native_formats import (
    check_pdf,
    check_identities,
    graphics_render,
    hide_text_paint,
    measurements,
)
from pipeline import Identities
from drawing_pdf import discover, UnsupportedPDF


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
