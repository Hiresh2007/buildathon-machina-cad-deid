from collections import Counter
import unittest

import pymupdf

from check_native_formats import verified_pdf
from drawing_pdf import discover, labelled_identity, rewrite
from pipeline import Identities


class InlineTitleField(unittest.TestCase):
    def test_values_are_consistent_and_engineering_labels_survive(self):
        fields = ["Designer: Mira Sanders", "Autor: Mira Sanders", "Empresa = Example Gear",
                  "Bauteilnummer: AB-157", "Dimension: 32 mm"]
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_line((14, 90), (200, 95))
            for n, text in enumerate(fields):
                page.insert_text((20, 110 + 22 * n), text)
            data = doc.tobytes()
        ids = Identities(b"consistent synthetic fixtures")
        seen = discover(data)
        for value in seen:
            ids.learn(value)
        for value in ("Mira Sanders", "Example Gear", "AB-157"):
            self.assertIn(value, seen)
        result = verified_pdf(data, rewrite(data, ids.alias, ids, lambda value: value), ids, Counter())
        with pymupdf.open(stream=result, filetype="pdf") as doc:
            text = doc[0].get_text()
            self.assertEqual(text.count(ids.alias("Mira Sanders")), 2)
            for value in ["Example Gear","AB-157"]:
                self.assertIn(ids.alias(value), text)
                self.assertNotIn(value,text)
            for label in ["Designer:", "Autor:", "Empresa =", "Bauteilnummer:", "Dimension: 32 mm"]:
                self.assertIn(label, text)

    def test_technical_fields_and_placeholder_values(self):
        for text in ["Designer: N/A", "Designer: design drawing", "Bauteilnummer: ISO4762",
                     "Bauteilnummer: 2025-09-11", "Dimension: 14 mm", "Firma:", "X: 14"]:
            self.assertIsNone(labelled_identity(text), text)
        self.assertIsNotNone(labelled_identity("Drawing number: W-091"))
        self.assertIsNotNone(labelled_identity("Designer: J. Smith"))


if __name__ == "__main__":
    unittest.main()
