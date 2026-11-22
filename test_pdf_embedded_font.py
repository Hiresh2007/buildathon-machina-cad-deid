"""An unsupported embedded font must keep its source drawing out of the cleaned package."""
import unittest

import pymupdf

import drawing_pdf
from pipeline import Identities


class EmbeddedFont(unittest.TestCase):
    def test_unrecognized_font_file_does_not_interrupt_the_batch(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((24, 44), "Author: Example Person")
            font = page.get_fonts()[0][0]
            payload = doc.get_new_xref()
            doc.update_object(payload, "<< /Subtype /Unrecognized >>")
            doc.update_stream(payload, b"unsupported")
            descriptor = doc.get_new_xref()
            doc.update_object(descriptor, f"<< /Type /FontDescriptor /FontFile3 {payload} 0 R >>")
            doc.xref_set_key(font, "FontDescriptor", f"{descriptor} 0 R")
            raw = doc.tobytes()
        ids = Identities(bytes(32))
        for word in ("Example Person", "Example", "Person"):
            ids.learn(word)
        with self.assertRaises(drawing_pdf.UnsupportedFont):
            drawing_pdf.rewrite(raw, ids.alias, ids, lambda value: value)


if __name__ == "__main__":
    unittest.main()
