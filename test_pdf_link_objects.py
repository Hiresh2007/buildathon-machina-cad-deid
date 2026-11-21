"""Regression for URI annotations on pages containing viewer-specific named actions."""
import unittest
import pymupdf
import drawing_pdf
from pipeline import Identities


class MissingLinkObject(unittest.TestCase):
    def test_ambiguous_link_array_returns_a_reportable_omission(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=160, height=160)
            for x in (12, 62):
                page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(x, 10, x+42, 22),
                                  "uri": "https://example.invalid/drawing"})
            _, named = page.annot_xrefs()
            doc.xref_set_key(named[0], "A", "<< /S /Named /N /ToggleFullScreen >>")
            raw = doc.tobytes()
        with pymupdf.open(stream=raw) as same:
            self.assertEqual([link["xref"] for link in same[0].get_links()], [0])
        identities = Identities(bytes(32))
        with self.assertRaises(drawing_pdf.UnindexedLink):
            drawing_pdf.rewrite(raw, identities.alias, identities, lambda value: value)


if __name__ == "__main__":
    unittest.main()
