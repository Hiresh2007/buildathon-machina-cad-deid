import unittest
from collections import Counter

import drawing_pdf
import pipeline
from check_native_formats import verified_pdf


def drawing(lines):
    with drawing_pdf.library().open() as doc:
        page = doc.new_page(width=530, height=350)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontsize=10)
        page.draw_line((50, 285), (460, 285), width=1)
        return doc.tobytes()


class PostalFields(unittest.TestCase):
    def test_independent_context_and_physical_replacement(self):
        first = drawing([(40, 45, '415 Cedar Avenue'), (40, 62, 'Example City, CA 94102'),
                         (40, 100, 'M8 x 30 mm'), (40, 150, 'Address: 62 Alder Rd.')])
        second = drawing([(40, 45, 'Send drawings to 415 Cedar Avenue for checking.'),
                          (40, 85, 'M8 x 30 mm')])
        ids = pipeline.Identities(b'address example key')
        for raw in (first, second):
            for value in drawing_pdf.discover(raw):
                ids.learn(value)
        for raw in (first, second):
            rewritten = drawing_pdf.rewrite(raw, ids.alias, ids, lambda value: value)
            final = verified_pdf(raw, rewritten, ids, Counter())
            with drawing_pdf.library().open(stream=final, filetype='pdf') as output:
                text = output[0].get_text()
                self.assertIn(ids.alias('415 Cedar Avenue'), text)
                self.assertNotIn('415 Cedar Avenue', text)
                self.assertIn('M8 x 30 mm', text)
                if raw == first:
                    self.assertIn(ids.alias('62 Alder Rd'), text)
                    self.assertNotEqual(ids.alias('62 Alder Rd'), ids.alias('415 Cedar Avenue'))

    def test_labels_foreign_order_and_false_positives(self):
        raw = drawing([(15, 40, 'Dirección: Calle Aurora 24'), (15, 75, '2 Bearing Street'),
                       (15, 90, 'Example City, CA 94107'), (15, 180, '23 Fixture Road'),
                       (260, 180, 'Example City, CA 94107'), (30, 265, '19 Cedar Street')])
        seen = drawing_pdf.discover(raw)
        self.assertIn('Calle Aurora 24', seen)
        for engineering in ('2 Bearing Street', '23 Fixture Road', '19 Cedar Street'):
            self.assertNotIn(engineering, seen)


if __name__ == '__main__':
    unittest.main()
