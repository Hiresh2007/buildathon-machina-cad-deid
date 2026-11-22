"""Reusing an independently verified PDF requires identical bytes and no relative references."""
import hashlib
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
import check_native_formats
import pipeline
import stream_corpus


class RepeatedDrawing(unittest.TestCase):
    def run_copies(self, linked):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((25, 55), "35 mm")
            if linked:
                page.insert_link({"kind": pymupdf.LINK_GOTOR, "from": pymupdf.Rect(10, 10, 40, 24),
                                  "file": "plate.step", "page": 0, "to": pymupdf.Point(0, 0)})
            data = doc.tobytes()
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary).resolve()
            args = SimpleNamespace(cache=cache, offline=True, verify_pdfs=True)
            ids = pipeline.Identities(bytes(32))
            paths = ["one/book.pdf", "two/more/book.pdf", "one/plate.step"]
            index = pipeline.PathIndex([PurePosixPath(path) for path in paths], ids)
            stream_corpus.start_writer(index)
            results = []
            with patch.object(check_native_formats, "verified_pdf", wraps=check_native_formats.verified_pdf) as verifier:
                for path in paths[:2]:
                    target = cache / path
                    target.parent.mkdir(parents=True)
                    target.write_bytes(data)
                    row = dict(path=path, ext=".pdf", bytes=len(data), md5=hashlib.md5(data).hexdigest(),
                               source_sha=hashlib.sha256(data).hexdigest())
                    results.append(stream_corpus.transformed(row, args))
                self.assertEqual([r[0] for r in results], ["staged", "staged"])
                self.assertEqual(verifier.call_count, 2 if linked else 1)
                changed = bytes(reversed(data))
                target.write_bytes(changed)
                self.assertEqual(stream_corpus.transformed(row, args)[0], "ready")
            return results

    def test_two_identical_unlinked_drawings(self):
        a, b = self.run_copies(False)
        self.assertEqual(a[2:], b[2:])

    def test_link_targets_follow_the_source_directory(self):
        a, b = self.run_copies(True)
        self.assertNotEqual(a[2], b[2])
        self.assertEqual(a[3]["resolved in input inventory"], 1)
        self.assertEqual(b[3]["resolved in input inventory"], 1)


if __name__ == "__main__":
    unittest.main()
