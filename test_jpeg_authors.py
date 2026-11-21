"""Exercise multi-author JPEG metadata, untagged images and identical drawing pixels."""
import shutil
import subprocess
import unittest
from collections import Counter

import pymupdf

import drawing_pdf
import jpeg_authors
import pdf_identity
from check_native_formats import check_pdf
from pipeline import Identities


@unittest.skipUnless(shutil.which("exiftool"), "embedded JPEG author tests require ExifTool")
class JPEGAuthorTest(unittest.TestCase):
    def test_authors_in_an_embedded_image(self):
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 12), False)
        pixmap.clear_with(82)
        plain = pixmap.tobytes("jpeg")
        ids = Identities(bytes(range(32)))
        self.assertIs(jpeg_authors.transform(plain, ids.alias, drawing_pdf.meaningful, True), plain)
        image = subprocess.run(
            ["exiftool", "-o", "-", "-IFD0:Artist=Fictional Painter", "-IFD0:XPAuthor=Fictional Painter",
             "-XMP-dc:Creator=Fictional Painter", "-XMP-dc:Creator+=Fictional Designer",
             "-IFD0:Orientation#=1", "-ExifIFD:UserComment=Calibration reference", "-"],
            input=plain, capture_output=True, check=True,
        ).stdout
        with pymupdf.open() as doc:
            page = doc.new_page(width=200, height=120)
            page.draw_rect(pymupdf.Rect(10, 15, 165, 100))
            page.insert_image(pymupdf.Rect(20, 20, 80, 65), stream=image)
            page.insert_text((10, 111), "25 mm")
            original = doc.tobytes()

        records = drawing_pdf.discover(original)
        self.assertTrue({"Fictional Painter", "Fictional Designer"}.issubset(records))
        for name in records:
            ids.learn(name)
        pdf_identity.resolve(records.forms, ids)
        result = drawing_pdf.rewrite(original, ids.alias, ids, lambda value: value)
        check_pdf(original, result, ids, Counter())
        with pymupdf.open(stream=result) as doc:
            jpeg = doc.xref_stream_raw(doc[0].get_images()[0][0])
            self.assertEqual(image[jpeg_authors.scan_offset(image):], jpeg[jpeg_authors.scan_offset(jpeg):])
            self.assertEqual(jpeg_authors.fields(jpeg), {
                "IFD0:Artist": [ids.alias("Fictional Painter")],
                "IFD0:XPAuthor": [ids.alias("Fictional Painter")],
                "XMP-dc:Creator": [ids.alias("Fictional Painter"), ids.alias("Fictional Designer")],
            })
            self.assertNotIn(b"Fictional Painter", jpeg)
            self.assertNotIn("Fictional Painter".encode("utf-16le"), jpeg)
            self.assertIn(b"Calibration reference", jpeg)
            orientation = subprocess.run(["exiftool", "-s3", "-IFD0:Orientation#", "-"],
                                         input=jpeg, capture_output=True, check=True)
            self.assertEqual(orientation.stdout.strip(), b"1")


if __name__ == "__main__":
    unittest.main()
