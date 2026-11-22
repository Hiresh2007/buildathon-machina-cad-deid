from pathlib import PurePosixPath
import unittest

import pdf_identity
from pipeline import Identities, PathIndex


class PartDescriptors(unittest.TestCase):
    def test_related_files_and_standalone_part_code(self):
        paths = [PurePosixPath('sources/provider/project', name) for name in (
            'QA-61 Front Cover.pdf', 'QA-61 Front Cover.step', 'QA-61 Rear Cover.pdf',
            'QA-62 Front Cover.pdf', 'QA-62 Front Cover.step',
            'QA-63 Front Cover.pdf', 'ISO 4014 Bolt.pdf', 'ISO 4014 Bolt.step',
            'M8 Bracket.pdf', 'M8 Bracket.step')]
        ids = Identities(bytes(32))
        pdf_identity.resolve([], ids, paths)
        index = PathIndex(paths, ids)
        first, model, rear, other = [PurePosixPath(index.targets[str(p)]).stem for p in paths[:4]]
        self.assertEqual(first, ids.alias('QA-61') + ' Front Cover')
        self.assertEqual(first, model)
        self.assertEqual(rear, ids.alias('QA-61') + ' Rear Cover')
        self.assertNotEqual(other, first)
        self.assertEqual(ids('Use QA-61 with QA-61 Front Cover'), 'Use ' + ids.alias('QA-61') + ' with ' + first)
        self.assertEqual(index.reference(str(paths[0]), paths[1].name), model + '.step')
        for name in ('QA-63 Front Cover', 'ISO 4014 Bolt', 'M8 Bracket'):
            self.assertNotIn(name.casefold(), ids.replacements)
        legacy = Identities(bytes(32))
        pdf_identity.resolve([], legacy, paths, link_parts=False)
        self.assertNotIn('qa-61 front cover', legacy.replacements)

    def test_different_collections_do_not_establish_a_pair(self):
        paths = [PurePosixPath('sources/provider/one/AX-71 Plate.pdf'),
                 PurePosixPath('sources/provider/two/AX-71 Plate.step')]
        ids = Identities(bytes(32))
        pdf_identity.resolve([], ids, paths)
        self.assertNotIn('ax-71 plate', ids.replacements)


if __name__ == '__main__':
    unittest.main()
