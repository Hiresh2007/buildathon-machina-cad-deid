"""Private synthetic regression for two-pass, rolling, resumable CAD processing."""
import contextlib
import hashlib
import io
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from unittest import mock
from urllib.parse import quote

import check
import pipeline
import stream_corpus as s


def table(db, sql):
    with contextlib.closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


def test():
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp)
        manifest = local / "manifest.tsv"
        cache = local / "input"
        attempt = local / "stage" / "attempt-1"
        archive = local / "external" / "attempt-1"
        state = local / "private"
        paths = {
            "sources/github/drawings/Alpha Model.step": check.document("Alpha Model.step", "../assemblies/Beta Model.stp"),
            "sources/github/assemblies/Beta Model.stp": check.document("Beta Model.stp", "../drawings/Alpha Model.step"),
            "sources/Gone.bin": b"unsupported",
            "sources/Damaged.step": b"broken",
        }
        # The early file's general free text mentions an identity discovered only
        # in the later file. Padding also forces two real, separate 1-MiB chunks.
        a, b, *_ = paths
        paths[a] = paths[a].replace(b"CASE-080 fixture", b"Violet Example fixture") + b" " * 600000
        paths[b] = paths[b].replace(b"Rae O''Neil", b"Violet Example") + b" " * 600000
        with manifest.open("w") as stream:
            for i, (name, data) in enumerate(paths.items()):
                file = cache / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(data)
                md5 = "" if i == 0 else hashlib.md5(data).hexdigest()
                stream.write(f"{name}\t{len(data)}\t{md5}\tcorpus/{quote(name)}\n")
        argv = ["--manifest", str(manifest), "--state", str(state), "--attempt", str(attempt),
                "--cache", str(cache), "--offline", "--chunk-mib", "1", "--workers", "2"]
        db = state / "checkpoint.sqlite3"

        def run(*flags, fails=False):
            args = s.options(argv + list(flags))
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    s.run(args)
            except s.Paused:
                if not fails:
                    raise
            else:
                assert not fails

        (cache / b).write_bytes(b"x" * len(paths[b]))
        run("--work-limit", "2", fails=True)
        run(fails=True)
        assert not list((attempt / "corpus").rglob("*.step"))
        assert table(db, "SELECT reason FROM files WHERE status='discover'") == [("organizer MD5 mismatch",)]
        (cache / b).write_bytes(paths[b])
        # First-pass digest pins multipart files even when their length is unchanged.
        (cache / a).write_bytes(paths[a].replace(b"Violet Example fixture", b"Yellow Example fixture"))
        run(fails=True)
        assert table(db, "SELECT reason FROM files WHERE status='ready'") == [("input differs from discovery snapshot",)]
        (cache / a).write_bytes(paths[a])
        run(fails=True)  # staged second output fills this chunk; no destination yet
        assert len(table(db, "SELECT 1 FROM files WHERE status='staged'")) == 1
        # Corrupted/stale staging is reverified and regenerated before export.
        f = next(f for f in (attempt / "corpus").rglob("*") if f.is_file())
        f.write_bytes(b"interrupted write")
        # An interrupted destination copy must leave the entire chunk retryable.
        with mock.patch.object(s, "local_copy", side_effect=OSError("injected interruption")):
            try:
                run("--archive", str(archive))
            except OSError:
                pass
            else:
                raise AssertionError("expected interruption")
        run("--archive", str(archive))
        run("--archive", str(archive))
        assert table(db, "SELECT status,COUNT(*) FROM files GROUP BY status") == [("delivered", 2), ("omitted", 2)]
        ids = pipeline.Identities((state / "alias.key").read_bytes())
        index = pipeline.PathIndex(list(map(PurePosixPath, paths)), ids)
        out_a = archive / "corpus" / index.targets[a]
        out_b = archive / "corpus" / index.targets[b]
        assert ids.alias("Violet Example").encode() in out_a.read_bytes()
        assert ids.alias("Violet Example").encode() in out_b.read_bytes()
        assert out_b.name.encode() in out_a.read_bytes()
        assert out_a.name.encode() in out_b.read_bytes()
        assert not any(f.is_file() for f in (attempt / "corpus").rglob("*"))
        assert "unsupported format" in (archive / "README.txt").read_text()
        assert "Damaged.step" not in (archive / "README.txt").read_text()
        assert {p.name for p in archive.iterdir()} == {"corpus", "code", "README.txt"}
        assert (archive / "code/stream_corpus.py").is_file()
    print("Cross-file, resume, mismatch, omission and chunk delivery checks passed.")


if __name__ == "__main__":
    test()
