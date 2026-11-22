"""Seed an independent full-manifest checkpoint from completed subset discovery."""

import argparse
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

import stream_corpus as stream


# These releases have the same byte-to-discovery behavior. Their assembly of the
# global inventory, output verification and scheduling may differ.
DISCOVERY_RELEASES = {
    "4d98f60a6bcab6a5baaf0ebde78e094e5967ac4a78e18303a0950cbe8c397fe2",  # 4112f54
    "d737445ee8d608dbb737353aa162bc2321c535378a9f1fe1363feadacdef7544",  # 9dbed71
    "4c12224e2c33430b4398493b26280fb304651bbb2b9d04c405aecd0d2025ffca",  # 672768a
    "90657a5e9e41d7594bb9062fe13f4dda097cd120fb22ae39e65d3e99f6424ea3",  # combined runner
}
# This earlier runner verified each input but used an older identity extractor.
HASH_RELEASES = {"2d267327475f7c28b75d5eb8bd3054319cdc0d84732afcb3f19109203dc8f40d"}  # 30bb3bb
STATIC = {"unsupported format", "over configured input size limit"}
SCANNED = {"invalid or unsupported document structure",
           "native metadata scanned; application streams require a native rewrite"}
REWRITE = {"unsupported embedded PDF font subtype", "unindexed PDF link could not be rewritten",
           "rewrite or structural validation failed", "independent PDF rendering or metadata validation failed"}


@dataclass
class Snapshot:
    meta: dict
    rows: list
    words: list
    forms: list


def read_snapshot(path, *, evidence):
    # Read a consistent transaction, then release the live runner's SQLite lock
    # before manipulating the snapshot or building the independent database.
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        meta = dict(db.execute("SELECT name,value FROM meta WHERE name IN ('inventory','manifest',"
                               "'source version','handlers','per-file limit','learn native properties')"))
        clause = "" if evidence else " WHERE source_sha IS NOT NULL"
        rows = db.execute("SELECT path,bytes,md5,url,ext,status,reason,source_sha FROM files" + clause).fetchall()
        words = db.execute("SELECT word FROM identity").fetchall() if evidence else []
        forms = db.execute("SELECT kind,spelling,target FROM pdf_identity_form").fetchall() if evidence else []
    return Snapshot(meta, rows, words, forms)


def release(meta):
    hashes = json.loads(meta["source version"])
    # These optional files affect only the independent output check. Compare
    # the optional native discovery handler separately whenever it was enabled.
    for name in ("check_native_formats.py", "requirements-verify.txt"):
        hashes.pop(name, None)
    native = hashes.pop("inspect_native.py", None)
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), native


def check_release(snapshot, target, evidence):
    meta = snapshot.meta
    if meta.get("inventory") != "complete" or "source version" not in meta:
        raise ValueError("incomplete input checkpoint inventory")
    source, native = release(meta)
    current, target_native = release(target)
    if evidence:
        for field in ("handlers", "per-file limit", "learn native properties"):
            if meta.get(field) != target[field]:
                raise ValueError("incompatible discovery configuration")
        if native != target_native or (source != current and not {source, current} <= DISCOVERY_RELEASES):
            raise ValueError("incompatible discovery source")
    elif meta.get("manifest") != target["manifest"] or (
            source != current and source not in DISCOVERY_RELEASES | HASH_RELEASES):
        raise ValueError("incompatible full-manifest source-hash checkpoint")


def discovery_outcome(status, reason, sha):
    if not sha:
        if status == "omitted" and reason in STATIC:
            return status, reason
        raise ValueError("donor discovery is incomplete")
    if status in ("ready", "staged", "delivered") or (status == "omitted" and reason in REWRITE):
        return "ready", ""
    if status == "omitted" and reason in SCANNED:
        return status, reason
    raise ValueError("unknown donor discovery outcome")


def populate(db, donor, previous=None):
    meta = dict(db.execute("SELECT name,value FROM meta"))
    check_release(donor, meta, True)
    if previous:
        check_release(previous, meta, False)
    targets = {row[0]: row[1:] for row in db.execute(
        "SELECT path,bytes,md5,url,ext,id,status,reason FROM files")}
    outcomes = {}
    pins = {}

    def match(row):
        path, size, md5, url, ext, status, reason, sha = row
        target = targets.get(path)
        if target is None or target[:4] != (size, md5, url, ext):
            raise ValueError("source record differs from the target manifest")
        if sha is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", sha) or target[5] != "discover":
                raise ValueError("invalid verified source record")
            if path in pins and pins[path] != sha:
                raise ValueError("source SHA-256 disagrees between checkpoints")
            pins[path] = sha
        return target

    for row in donor.rows:
        target = match(row)
        ext, status, reason, sha = row[4:]
        result = discovery_outcome(status, reason, sha)
        if sha is None:
            if target[5:] != result:
                raise ValueError("incompatible static omission")
        else:
            content = (ext, row[1], sha)
            if outcomes.setdefault(content, result) != result:
                raise ValueError("identical donor inputs have inconsistent discovery outcomes")
    if previous:
        for row in previous.rows:
            match(row)

    ready = []
    pending = []
    copies = []
    representatives = {}
    for path, sha in pins.items():
        size, _, _, ext, file_id, _, _ = targets[path]
        content = (ext, size, sha)
        if content in outcomes:
            ready.append((*outcomes[content], sha, file_id))
        else:
            pending.append((sha, file_id))
            first = representatives.setdefault(content, file_id)
            if first != file_id:
                copies.append((file_id, first))
    with db:
        db.executemany("INSERT INTO identity VALUES (?)", donor.words)
        db.executemany("INSERT INTO pdf_identity_form VALUES (?,?,?)", donor.forms)
        db.executemany("UPDATE files SET status=?,reason=?,source_sha=? WHERE id=?", ready)
        db.executemany("UPDATE files SET source_sha=? WHERE id=?", pending)
        if copies:
            db.execute("CREATE TABLE discovery_copy(file_id INTEGER PRIMARY KEY, representative INTEGER NOT NULL)")
            db.execute("CREATE INDEX discovery_representative ON discovery_copy(representative)")
            db.executemany("INSERT INTO discovery_copy VALUES (?,?)", copies)
            stream.bind(db, "verified discovery copies", "present")
        stream.bind(db, "discovery donor manifest", donor.meta["manifest"])
        stream.bind(db, "discovery seed implementation", stream.digest(Path(__file__)))
    return {"subset_rows": len(donor.rows), "verified_results_reused": len(ready),
            "verified_copies_to_share": len(copies), "remaining_distinct_reads": db.execute(
                "SELECT COUNT(*) FROM files WHERE status='discover'").fetchone()[0] - len(copies)}


def seed(args, donor_path, previous_path=None):
    os.umask(0o077)
    for dest in (args.state, args.attempt):
        if dest.exists():
            raise ValueError("use new, independent state and attempt paths")
        dest.parent.mkdir(parents=True, exist_ok=True)
    if args.key_file != args.state / "alias.key":
        raise ValueError("the new state generates its own private alias key")
    donor = read_snapshot(donor_path, evidence=True)
    previous = read_snapshot(previous_path, evidence=False) if previous_path else None
    # Build in a sibling temporary directory and publish only a complete state.
    with tempfile.TemporaryDirectory(dir=args.state.parent) as tmp:
        private = Path(tmp) / "state"
        private.mkdir(mode=0o700)
        args.attempt.mkdir(mode=0o700)
        try:
            key = stream.pipeline._load_key(private / "alias.key")
            with contextlib.closing(sqlite3.connect(private / "checkpoint.sqlite3")) as db:
                page = db.execute("PRAGMA page_size").fetchone()[0]
                db.execute(f"PRAGMA max_page_count={args.state_mib * stream.MIB // page}")
                stream.init_db(db, argparse.Namespace(**(vars(args) | {"state": private})), key)
                result = populate(db, donor, previous)
            if args.state.exists():
                raise ValueError("destination state already exists")
            private.rename(args.state)
        except BaseException:
            args.attempt.rmdir()
            raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--donor", type=Path, required=True, help="compatible completed-discovery subset database")
    parser.add_argument("--source-hashes", type=Path, help="optional verified SHA-256s from an earlier full run")
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
    print(json.dumps(seed(stream.options(argv), flags.donor, flags.source_hashes), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, stream.Paused) as error:
        sys.exit(f"Failed: {error}")
