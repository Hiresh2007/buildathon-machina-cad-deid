"""Rebuild just the complete collections and detect a changed archived model."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from export_collections import selection


class ExportCollections(unittest.TestCase):
    def test_complete_collections_and_input_hashes(self):
        source = Path(__file__).resolve().parent
        payload = b"sanitized drawing fixture"
        checksum = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "snapshot.sqlite3"
            with sqlite3.connect(state) as db:
                db.executescript("CREATE TABLE meta(name,value); CREATE TABLE files(id,path,ext,status,reason,target,output_bytes,output_sha);")
                db.executemany("INSERT INTO meta VALUES (?,?)", [
                    ("source version", json.dumps({"step.py": hashlib.sha256((source / "step.py").read_bytes()).hexdigest()})),
                    ("inventory", "complete"), ("manifest", "test manifest")])
                db.executemany("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)", [
                    (1, "sources/provider/one/a.pdf", ".pdf", "delivered", "", "clean/one.pdf", len(payload), checksum),
                    (2, "sources/provider/two/b.pdf", ".pdf", "ready", "", None, None, None),
                    (3, "sources/provider/one/c.pdf", ".pdf", "omitted", "unsupported document", None, None, None),
                    (4, "sources/provider/two/d.pdf", ".pdf", "ready", "", None, None, None)])
            kept, stats = selection(state, 3)
            self.assertEqual(len(kept), 1)
            self.assertEqual(stats["selected"], 2)
            self.assertEqual(stats["collections"], 1)
            with self.assertRaises(ValueError):
                selection(state, 4)
            archive = root / "archive"
            model = archive / "corpus/clean/one.pdf"
            model.parent.mkdir(parents=True)
            model.write_bytes(payload)
            export = root / "out"
            cmd = [sys.executable, str(source / "export_collections.py"), "--checkpoint", str(state), "--archive", str(archive),
                   "--through-row", "3", "--output", str(export)]
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual((export / "corpus/clean/one.pdf").read_bytes(), payload)
            self.assertTrue((export / "code/export_collections.py").is_file())
            report = (export / "README.txt").read_text()
            self.assertIn("Selected 2 inputs", report)
            self.assertNotIn("sources/provider/one", report)
            model.write_bytes(b"X" * len(payload))
            cmd[-1] = str(root / "corrupt")
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
            self.assertFalse((root / "corrupt").exists())


if __name__ == "__main__":
    unittest.main()
