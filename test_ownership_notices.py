import unittest
import pymupdf
import drawing_pdf
import pipeline
import pdf_identity
from check_native_formats import verified_pdf
from collections import Counter


class OwnershipNotices(unittest.TestCase):
    def test_only_the_named_company_is_replaced(self):
        rows = ["Copyright (C) 2017-2020 by Example Gear, Inc. All rights reserved.",
                "This drawing is the sole property of Second Works GmbH.",
                "Second Works GmbH", "54 mm"]
        with pymupdf.open() as source:
            p = source.new_page()
            for i, text in enumerate(rows):
                p.insert_text((32, 40 + 25 * i), text)
            p.draw_line((20, 180), (130, 180))
            data = source.tobytes()
        ids = pipeline.Identities(b"only synthetic engineering drawings")
        observations = drawing_pdf.discover(data)
        self.assertIn("Example Gear, Inc", observations)
        self.assertIn("Second Works GmbH", observations)
        self.assertNotIn(rows[1], observations)
        for name in observations:
            ids.learn(name)
        for legal in ("property of", "copyright", "all rights reserved"):
            ids.learn(legal)
        pdf_identity.resolve(observations.forms, ids)
        self.assertEqual(ids.alias("Example Gear, Inc."), ids.alias("Example Gear, Inc"))
        self.assertNotEqual(ids.alias("Different Example, Inc."), ids.alias("Example Gear, Inc"))
        output = drawing_pdf.rewrite(data, ids.alias, ids, lambda value:value)
        output = verified_pdf(data, output, ids, Counter())
        with pymupdf.open(stream=output, filetype="pdf") as doc:
            text = doc[0].get_text()
            for original in ("Example Gear", "Second Works"):
                self.assertNotIn(original, text)
            self.assertIn("2017-2020",text)
            self.assertIn("All rights reserved",text)
            self.assertIn("sole property of",text)
            self.assertIn("54 mm",text)
            self.assertEqual(text.count(ids.alias("Second Works GmbH")),2)

    def test_an_independent_contact_after_the_notice_is_still_replaced(self):
        ids = pipeline.Identities(b"two independently observed names")
        line = "Copyright 2020 Example Parts Inc. Contact qa@example.test"
        edits = [(line[a:b], replacement) for a,b,replacement in drawing_pdf.matches(line,ids,ids.alias)]
        self.assertIn(("Example Parts Inc",ids.alias("Example Parts Inc")),edits)
        self.assertIn(("qa@example.test",ids.alias("qa@example.test")),edits)

    def test_second_legal_clause_preserves_its_marker(self):
        ids = pipeline.Identities(b"two independent clauses")
        ids.learn("the property of")
        line = "Copyright 2018 Example Parts Inc. Marks are the property of their respective owners."
        changes = list(drawing_pdf.matches(line, ids, ids.alias))
        self.assertEqual([(line[a:b], v) for a,b,v in changes],
                         [("Example Parts Inc", ids.alias("Example Parts Inc"))])

    def test_technical_language_and_missing_owners(self):
        for value in ("PROPRIETARY TO THE CUSTOMER", "Material property of steel, 50 mm",
                      "Copyright 2020. All rights reserved by Example, Inc.",
                      "Copyright 2018-2020", "Length to datum C: 40 mm"):
            self.assertEqual(drawing_pdf.notice_owners(value), [])
        text = "© 2009 Example S.p.A. / © 2011 Other Parts LLC."
        self.assertEqual([text[a:b] for a,b in drawing_pdf.notice_owners(text)], ["Example S.p.A","Other Parts LLC"])


if __name__=="__main__":
    unittest.main()
