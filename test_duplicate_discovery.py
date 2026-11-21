"""Use committed discovery evidence only after verifying the bytes of each physical file."""
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check import document
import stream_corpus


class DuplicateDiscovery(unittest.TestCase):
    def test_committed_evidence_requires_sha_and_valid_source(self):
        raw = document("fixture.step", "model.stp") + b" " * 140_000
        signature = hashlib.sha256(raw).hexdigest()
        md5 = hashlib.md5(raw).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            (folder / "fixture.step").write_bytes(raw)
            row = dict(path="fixture.step", ext=".step", md5=md5, bytes=len(raw), source_sha=None)
            args = SimpleNamespace(cache=folder, offline=True)
            stream_corpus._discoveries.clear()
            expected = stream_corpus.source_identities(row, args)
            self.assertTrue(expected[3])
            conn = sqlite3.connect(":memory:")
            conn.executescript("CREATE TABLE files(id,ext,md5,bytes,source_sha,status,reason);")
            conn.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", [
                (1, ".step", md5, len(raw), signature, "ready", ""),
                (2, ".step", md5, len(raw), None, "discover", ""),
                (3, ".step", md5, len(raw), signature, "omitted", "independent rewrite failure")])
            known = stream_corpus.duplicate_discoveries(conn)
            key = (".step", md5, len(raw))
            self.assertIsNone(known[key], "an older release must not supply the witness")
            known[key] = (signature, expected[0], expected[1])
            planned = next(stream_corpus.with_discovery_hints([row], known))
            self.assertEqual(planned["committed_discovery"], (signature, "ready", ""))
            stream_corpus._discoveries.clear()
            with patch.object(stream_corpus.pipeline.HANDLERS[".step"], "discover",
                              side_effect=AssertionError("parsed a verified duplicate")):
                self.assertEqual(stream_corpus.source_identities(planned, args), ("ready", "", signature, (), ()))
            planned["committed_discovery"] = ("0" * 64, "omitted", "invalid or unsupported document structure")
            self.assertEqual(stream_corpus.source_identities(planned, args), expected)
            (folder / "fixture.step").write_bytes(raw[:-1] + b"X")
            self.assertEqual(stream_corpus.source_identities(planned, args)[0], "discover")
            conn.close()


if __name__ == "__main__":
    unittest.main()
