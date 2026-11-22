import contextlib
import hashlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

import check
import seed_discovery as seed
import stream_corpus as stream


def discovery(args, limit):
    args.work_limit = limit
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            stream.run(args)
        except stream.Paused as error:
            return str(error)
    raise AssertionError("bounded discovery unexpectedly entered the output phase")


def tables(args):
    with contextlib.closing(sqlite3.connect(args.state / "checkpoint.sqlite3")) as db:
        return (db.execute("SELECT path,status,reason,source_sha FROM files ORDER BY path").fetchall(),
                db.execute("SELECT * FROM identity ORDER BY word").fetchall(),
                db.execute("SELECT * FROM pdf_identity_form ORDER BY kind,spelling,target").fetchall())


class SeedDiscovery(unittest.TestCase):
    def test_verified_subset_recovers_fresh_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "input"
            cache.mkdir()
            document = check.document("A.step", "B.step")
            other = document.replace(b"Rae O''Neil", b"lead@fixtures.test")
            files = {"A.step": document, "ImportedCopy.step": document, "B.step": other, "SecondB.step": other,
                     "Later.stp": other, "Bad.step": b"invalid", "Ignored.bin": b"opaque"}
            for name, data in files.items():
                (cache / name).write_bytes(data)

            def args(label, selected):
                manifest = root / (label + ".tsv")
                with manifest.open("w") as out:
                    for name in selected:
                        data = files[name]
                        md5 = "" if name in ("B.step", "SecondB.step") else hashlib.md5(data).hexdigest()
                        out.write(f"{name}\t{len(data)}\t{md5}\tcorpus/{quote(name)}\n")
                return stream.options(["--manifest", str(manifest), "--attempt", str(root / label / "attempt-1"),
                                       "--state", str(root / (label + "-state")), "--cache", str(cache),
                                       "--offline", "--workers", "1", "--reserve-mib", "0"])

            baseline = args("baseline", files)
            self.assertEqual(discovery(baseline, 6), "work limit reached")
            expected = tables(baseline)
            subset = args("donor", ["A.step", "Bad.step", "Ignored.bin"])
            self.assertEqual(discovery(subset, 2), "work limit reached")
            donor = subset.state / "checkpoint.sqlite3"
            previous = baseline.state / "checkpoint.sqlite3"
            with contextlib.closing(sqlite3.connect(donor)) as db, db:
                db.execute("UPDATE files SET status='omitted',reason='rewrite or structural validation failed'"
                           " WHERE path='A.step'")
            with contextlib.closing(sqlite3.connect(previous)) as db, db:
                db.execute("INSERT INTO identity VALUES (?)", (b"obsolete prior observation",))
                db.execute("INSERT INTO pdf_identity_form VALUES (?,?,?)", ("hostname", b"obsolete.test", b"obsolete.test"))
                db.execute("UPDATE files SET status='discover',source_sha=NULL WHERE path='Later.stp'")
            target = args("combined", files)
            stats = seed.seed(target, donor, previous)
            self.assertEqual(stats, {"subset_rows": 3, "verified_results_reused": 3,
                                     "verified_copies_to_share": 1, "remaining_distinct_reads": 2})
            self.assertEqual(next(row for row in tables(target)[0] if row[0] == "A.step")[1:3], ("ready", ""))
            (cache / "B.step").write_bytes(b"X" * len(other))
            self.assertIn("unavailable", discovery(target, 3))
            pending = {row[0]: row[1:] for row in tables(target)[0]}
            self.assertEqual(pending["B.step"][0:2], ("discover", "input differs from discovery snapshot"))
            self.assertEqual(pending["B.step"][2], hashlib.sha256(other).hexdigest())
            self.assertEqual(pending["SecondB.step"][0], "discover")
            (cache / "B.step").write_bytes(other)
            self.assertEqual(discovery(target, 1), "work limit reached")
            self.assertEqual(tables(target), expected)

            with self.assertRaisesRegex(ValueError, "new, independent"):
                seed.seed(target, donor, previous)
            broken = args("mismatch", files)
            with contextlib.closing(sqlite3.connect(donor)) as db, db:
                db.execute("UPDATE files SET source_sha=? WHERE path='A.step'", ("a" * 64,))
            with self.assertRaisesRegex(ValueError, "disagrees"):
                seed.seed(broken, donor, previous)
            self.assertFalse(broken.state.exists())
            self.assertFalse(broken.attempt.exists())
            with contextlib.closing(sqlite3.connect(donor)) as db, db:
                db.execute("UPDATE files SET source_sha=NULL,status='discover',reason='' WHERE path='A.step'")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                seed.seed(broken, donor, previous)
            with contextlib.closing(sqlite3.connect(donor)) as db, db:
                db.execute("UPDATE meta SET value='{}' WHERE name='source version'")
            with self.assertRaisesRegex(ValueError, "incompatible discovery source"):
                seed.seed(broken, donor, previous)


if __name__ == "__main__":
    unittest.main()
