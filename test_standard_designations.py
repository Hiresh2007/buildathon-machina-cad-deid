import unittest
from pathlib import PurePosixPath
import step
from naming import stock_designation
from pipeline import Identities, PathIndex


class StandardDesignations(unittest.TestCase):
    def test_catalog_names_survive_relationship_and_model_identity_processing(self):
        ids = Identities(b"synthetic stock geometry")
        for label in ("ISO_4762_socket_head_cap_screw_M6x20", "Screw M6x20 ISO 4762 8.8",
                      "Flat bar 60x8 EN 10058 S235JR", "Round_Bar_EN10060_D20",
                      "Square hollow section 60x60x3 EN 10219 S355J2H", "Bolt M8x35 ISO 4014"):
            with self.subTest(label=label):
                self.assertTrue(stock_designation(label))
                ids.learn(label)
                self.assertNotEqual(ids.alias(label),label)
                self.assertEqual(ids(label),label)
                folder = PurePosixPath("sources/example/project")
                drawing = folder / (label + ".pdf")
                part = folder / (label + ".step")
                index = PathIndex([drawing, part],ids)
                self.assertEqual(PurePosixPath(index.targets[str(part)]).name,part.name)
                self.assertEqual(index.reference(str(drawing),part.name),part.name)

    def test_explicit_author_and_product_roles_stay_separate(self):
        label = "ISO_4762_socket_head_cap_screw_M6x20"
        source = ("ISO-10303-21;HEADER;FILE_DESCRIPTION(('fixture'),'2;1');"
                  "FILE_NAME('model.step','2015-01-01',('" + label + "'),('Company X'),'CAD','CAD','');"
                  "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));ENDSEC;DATA;"
                  "#1=PRODUCT('" + label + "','" + label + "','',());ENDSEC;END-ISO-10303-21;").encode()
        ids = Identities(b"independent STEP roles")
        for word in step.discover(source):
            ids.learn(word)
        output = step.rewrite(source,ids.alias,ids,lambda name:name)
        self.assertEqual(output.count(label.encode()),2)
        self.assertEqual(output.count(ids.alias(label).encode()),1)
        self.assertNotIn(b"Company X",output)

    def test_known_standard_number_alone_cannot_exempt_a_project_or_person(self):
        ids = Identities(b"synthetic stock geometry")
        for label in ("Example Company ISO 4762", "Screw M6x20 ISO 4762 Project Jane",
                      "Screw M6x20 ISO 4799", "Unknown EN 10058",
                      "Flat bar 60x8 ISO 4762", "ISO 2020", "M8-746320-ISO4762"):
            self.assertFalse(stock_designation(label))
            self.assertNotEqual(ids.alias(label),label)


if __name__ == "__main__":
    unittest.main()
