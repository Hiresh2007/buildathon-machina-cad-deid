"""A valid, zero-byte XML metadata stream can accompany an ordinary drawing."""
from collections import Counter
import unittest

import pymupdf

from check_native_formats import check_pdf
from drawing_pdf import discover, rewrite
from pipeline import Identities


class EmptyMetadata(unittest.TestCase):
    def test_empty_xml_stream_preserves_geometry_and_other_metadata_is_cleaned(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_line((24, 36), (88, 108))
            page.insert_text((22, 150), "12 mm")
            doc.set_metadata({"author": "Example Person"})
            metadata = doc.get_new_xref()
            doc.update_object(metadata, "<< /Type /Metadata /Subtype /XML >>")
            doc.update_stream(metadata, b"")
            doc.xref_set_key(doc.pdf_catalog(), "Metadata", f"{metadata} 0 R")
            source = doc.tobytes()
        ids = Identities(bytes(32))
        for word in discover(source):
            ids.learn(word)
        cleaned = rewrite(source, ids.alias, ids, lambda value: value)
        check_pdf(source, cleaned, ids, Counter())
        with pymupdf.open(stream=cleaned, filetype="pdf") as doc:
            self.assertNotEqual(doc.metadata["author"], "Example Person")
            self.assertIn("12 mm", doc[0].get_text())
            xml_streams = [doc.xref_stream(xref) for xref in range(1, doc.xref_length())
                           if doc.xref_get_key(xref, "Type") == ("name", "/Metadata")]
            self.assertEqual(xml_streams, [b""])


if __name__ == "__main__":
    unittest.main()
