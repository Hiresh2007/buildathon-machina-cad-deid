import hashlib
from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pipeline
import stream_corpus


class RepeatedOutput(unittest.TestCase):
    def rows(self, svg):
        sha = hashlib.sha256(svg).hexdigest()
        paths = ["one/item.svg", "two/maps/item.svg", "one/diagram.svg"]
        index = pipeline.PathIndex([PurePosixPath(path) for path in paths], pipeline.Identities(bytes(32)))
        stream_corpus.start_writer(index, {(".svg", sha)})
        return [dict(path=path, ext=".svg", bytes=len(svg), md5=hashlib.md5(svg).hexdigest(),
                     source_sha=sha, prefetched_input=svg) for path in paths[:2]]

    def test_each_hit_still_checks_the_source_and_resets_with_the_inventory(self):
        data = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L2 2"/></svg>'
        first, second = self.rows(data)
        options = SimpleNamespace(verify_pdfs=True)
        with patch.object(pipeline.svg, "rewrite", wraps=pipeline.svg.rewrite) as rewrite:
            self.assertEqual(stream_corpus.transformed(first, options), stream_corpus.transformed(second, options))
            self.assertEqual(rewrite.call_count, 1)
            damaged = dict(second, prefetched_input=data.replace(b"2 2", b"2 3"))
            self.assertEqual(stream_corpus.transformed(damaged, options)[0], "ready")
            self.rows(data)
            self.assertEqual(stream_corpus.transformed(second, options)[0], "staged")
            self.assertEqual(rewrite.call_count, 2)

    def test_a_relative_reference_prevents_reuse(self):
        data = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                b'<image xlink:href="diagram.svg" width="2" height="2"/></svg>')
        first, second = self.rows(data)
        options = SimpleNamespace(verify_pdfs=True)
        with patch.object(pipeline.svg, "rewrite", wraps=pipeline.svg.rewrite) as rewrite:
            left = stream_corpus.transformed(first, options)
            right = stream_corpus.transformed(second, options)
        self.assertEqual((left[0], right[0]), ("staged", "staged"))
        self.assertEqual(rewrite.call_count, 2)
        self.assertNotEqual(left[2], right[2])
        self.assertEqual(left[3]["resolved in input inventory"], 1)
        self.assertEqual(right[3]["resolved in input inventory"], 1)


if __name__ == "__main__":
    unittest.main()
