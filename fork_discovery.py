"""Fork a pre-rewrite checkpoint for the compatible full-manifest path resolver."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

import stream_corpus as stream


# The path release changes inventory resolution and rewriting. The original
# byte parsers, canonicalization and source verification have the same behavior.
PREVIOUS = "2d267327475f7c28b75d5eb8bd3054319cdc0d84732afcb3f19109203dc8f40d"  # 30bb3bb
CURRENT = "7da5a1e5d907e757075d35ad1690807a2de98dc5241746ebf6837d82935ae08d"


def fingerprint(hashes):
    optional = {"inspect_native.py", "requirements-verify.txt", "check_native_formats.py"}
    common = {name: value for name, value in hashes.items() if name not in optional}
    return hashlib.sha256(json.dumps(common, sort_keys=True).encode()).hexdigest()


def check(db):
    meta = dict(db.execute("SELECT name,value FROM meta"))
    required = {"manifest", "key", "attempt", "per-file limit", "handlers", "learn native properties",
                "independent PDF verification", "source version", "inventory"}
    if set(meta) != required or meta["inventory"] != "complete":
        raise ValueError("expected a complete manifest inventory before any delivery")
    if db.execute("SELECT 1 FROM files WHERE status NOT IN ('discover','ready','omitted') OR target IS NOT NULL "
                  "OR output_sha IS NOT NULL OR output_bytes IS NOT NULL OR resolved<>0 OR unresolved<>0 "
                  "OR (status='omitted' AND reason NOT IN ('invalid or unsupported document structure',"
                  "'native metadata scanned; application streams require a native rewrite',"
                  "'unsupported format','over configured input size limit')) LIMIT 1").fetchone():
        raise ValueError("source checkpoint has already rewritten files")
    previous = json.loads(meta["source version"])
    if fingerprint(previous) != PREVIOUS:
        raise ValueError("input release is outside this verified discovery migration")
    current = {name: stream.digest(stream.SOURCE / name) for name in previous}
    if (fingerprint(current) != CURRENT
            or any(previous.get(name) != current.get(name) for name in
                   ("inspect_native.py", "requirements-verify.txt", "check_native_formats.py"))):
        raise ValueError("input or destination release is outside this verified discovery migration")


def fork(source, args):
    os.umask(0o077)
    if args.state.exists() or args.attempt.exists() or args.key_file != args.state / "alias.key":
        raise ValueError("use new, independent state and attempt paths and their generated key")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.attempt.parent.mkdir(parents=True, exist_ok=True)
    stream.check_space(args.state.parent, source.stat().st_size, args)
    with tempfile.TemporaryDirectory(dir=args.state.parent) as tmp:
        private = Path(tmp) / "state"
        private.mkdir(mode=0o700)
        with contextlib.closing(sqlite3.connect(private / "checkpoint.sqlite3")) as db:
            with contextlib.closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
                old.backup(db, pages=256)
            check(db)
            page = db.execute("PRAGMA page_size").fetchone()[0]
            cap = args.state_mib * stream.MIB // page
            if db.execute(f"PRAGMA max_page_count={cap}").fetchone()[0] > cap:
                raise ValueError("snapshot exceeds the destination checkpoint size limit")
            with db:
                db.execute("DELETE FROM meta WHERE name IN ('attempt','key','source version')")
            args.attempt.mkdir(mode=0o700)
            try:
                key = stream.pipeline._load_key(private / "alias.key")
                stream.init_db(db, argparse.Namespace(**(vars(args) | {"state": private})), key)
                stats = stream.response_summary(db)
            except BaseException:
                args.attempt.rmdir()
                raise
        try:
            if args.state.exists():
                raise ValueError("destination state already exists")
            private.rename(args.state)
        except BaseException:
            args.attempt.rmdir()
            raise
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--max-mib", type=int, default=64)
    parser.add_argument("--state-mib", type=int, default=1024)
    parser.add_argument("--learn-native", action="store_true")
    parser.add_argument("--verify-pdfs", action="store_true")
    flags = parser.parse_args()
    argv = [item for name in ("manifest", "state", "attempt", "max_mib", "state_mib")
            for item in ("--" + name.replace("_", "-"), str(getattr(flags, name)))]
    argv.extend("--" + name.replace("_", "-") for name in ("learn_native", "verify_pdfs") if getattr(flags, name))
    print(json.dumps(fork(flags.source, stream.options(argv)), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, stream.Paused) as error:
        sys.exit(f"Failed: {error}")
