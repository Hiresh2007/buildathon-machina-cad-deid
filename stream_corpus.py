"""Process an organizer manifest with a private checkpoint and bounded local staging."""
import argparse
import contextlib
import fcntl
import hashlib
import http.client
import itertools
import json
import multiprocessing
from collections import Counter, OrderedDict, deque
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from urllib.parse import unquote, urlsplit

import pipeline

NATIVE_TYPES = {'.sldprt', '.sldasm', '.slddrw', '.ipt', '.iam', '.idw'}

MIB = 1024 * 1024
_discoveries = OrderedDict()
MIRROR = "https://machina-input.joinbuilders.org/"
_connections = threading.local()
UPLOADER_SHA = "a22d91f16bdf4220f73b9afa243a393e5c9143546f4ba48a70ec76f70edacc05"
SOURCE = Path(__file__).resolve().parent


class Paused(Exception):
    """The checkpoint is intact; a subsequent invocation can resume."""


class SourceError(Exception):
    """A source could not be fetched or verified."""


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(MIB), b""):
            sha.update(block)
    return sha.hexdigest()


def manifest_rows(manifest):
    with manifest.open(encoding="utf-8", newline="") as stream:
        for line in stream:
            if line.startswith("#"):
                continue
            path, size, md5, url = line.rstrip("\r\n").split("\t")
            parts = path.split("/")
            if (not path or "\\" in path or any(p in ("", ".", "..") for p in parts)
                    or any(ord(ch) < 32 for ch in path) or not size.isdecimal()
                    or (md5 and not re.fullmatch(r"[0-9a-fA-F]{32}", md5))):
                raise ValueError("invalid manifest path, size or checksum")
            remote = urlsplit(url)
            if remote.scheme or remote.netloc or remote.query or remote.fragment or unquote(url) != "corpus/" + path:
                raise ValueError("invalid organizer mirror-relative URL")
            yield path, int(size), md5.lower(), url, PurePosixPath(path).suffix.lower()


def bind(db, name, value):
    old = db.execute("SELECT value FROM meta WHERE name=?", (name,)).fetchone()
    if old and old[0] != value:
        raise ValueError(f"checkpoint belongs to a different {name}")
    db.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (name, value))


def init_db(db, args, key):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS meta(name TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS identity(word TEXT PRIMARY KEY) WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS pdf_identity_form(kind TEXT, spelling BLOB, target BLOB,
          PRIMARY KEY(kind,spelling,target)) WITHOUT ROWID;
      CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, bytes INTEGER,
          md5 TEXT, url TEXT, ext TEXT, status TEXT, reason TEXT DEFAULT '', source_sha TEXT,
          target TEXT, output_sha TEXT, output_bytes INTEGER, resolved INTEGER DEFAULT 0,
          unresolved INTEGER DEFAULT 0);
      CREATE INDEX IF NOT EXISTS work ON files(status,id);
    """)
    code = {Path(pipeline.__file__), Path(__file__), SOURCE / "naming.py", SOURCE / "pdf_identity.py", SOURCE / "requirements.txt"}
    code.update(Path(handler.__file__) for handler in pipeline.HANDLERS.values())
    if args.learn_native:
        code.add(SOURCE / "inspect_native.py")
    if args.verify_pdfs:
        code.update((SOURCE / 'check_native_formats.py', SOURCE / 'requirements-verify.txt'))
    with db:
        bind(db, "manifest", digest(args.manifest))
        bind(db, "key", hashlib.sha256(key).hexdigest())
        bind(db, "attempt", str(args.attempt))
        bind(db, "per-file limit", str(args.max_mib))
        bind(db, "handlers", json.dumps(sorted(pipeline.HANDLERS)))
        bind(db, "learn native properties", str(args.learn_native))
        bind(db, "independent PDF verification", str(args.verify_pdfs))
        bind(db, "source version", json.dumps({p.name: digest(p) for p in sorted(code)}, sort_keys=True))
    if db.execute("SELECT 1 FROM meta WHERE name='inventory'").fetchone():
        return
    if any(args.attempt.iterdir()):
        raise ValueError("a new checkpoint requires an empty attempt staging directory")
    rows = enumerate(manifest_rows(args.manifest), 1)
    saved = db.execute("SELECT COALESCE(MAX(id),0) FROM files").fetchone()[0]
    for _ in itertools.islice(rows, saved):
        pass
    while batch := list(itertools.islice(rows, 500)):
        check_space(args.state, MIB, args)
        entries = []
        for i, (path, size, md5, url, ext) in batch:
            supported = ext in pipeline.HANDLERS or (args.learn_native and ext in NATIVE_TYPES)
            reason = ("unsupported format" if not supported else
                      "over configured input size limit" if size > args.max_mib * MIB else "")
            entries.append((i, path, size, md5, url, ext, "omitted" if reason else "discover", reason))
        with db:
            db.executemany("INSERT INTO files(id,path,bytes,md5,url,ext,status,reason) VALUES(?,?,?,?,?,?,?,?)", entries)
    with db:
        bind(db, "inventory", "complete")


def verified(row, data):
    if len(data) != row["bytes"]:
        raise SourceError("input size mismatch")
    if row["md5"] and hashlib.md5(data).hexdigest() != row["md5"]:
        raise SourceError("organizer MD5 mismatch")
    sha = hashlib.sha256(data).hexdigest()
    if row["source_sha"] and sha != row["source_sha"]:
        raise SourceError("input differs from discovery snapshot")
    return data, sha


def mirror_bytes(row, timeout):
    # Each thread or worker process owns one keep-alive connection. The path
    # comes from the already validated official manifest and stays on this host.
    if not getattr(_connections, 'http', None):
        if not getattr(_connections, 'tls', None):
            _connections.tls = ssl.create_default_context()
        _connections.http = http.client.HTTPSConnection(urlsplit(MIRROR).hostname,
                                                       timeout=min(timeout, 10), context=_connections.tls)
    connection = _connections.http
    end = time.monotonic() + timeout
    connection.request('GET', '/' + row['url'], headers={'Accept-Encoding': 'identity'})
    response = connection.getresponse()
    if response.status != 200:
        raise SourceError(f'mirror HTTP {response.status}')
    expected = row['bytes']
    if response.length is not None and response.length != expected:
        raise SourceError('mirror size differs from manifest')
    data = bytearray()
    while len(data) <= expected:
        seconds = end - time.monotonic()
        if seconds <= 0:
            raise TimeoutError('source read timed out')
        if connection.sock:
            connection.sock.settimeout(seconds)
        block = response.read(min(MIB, expected + 1 - len(data)))
        if not block:
            break
        data.extend(block)
    return bytes(data)


def fetch(row, args):
    reason = "missing from cache"
    if args.cache:
        candidate = args.cache / row["path"]
        try:
            if candidate.is_file() and candidate.resolve().is_relative_to(args.cache):
                if candidate.stat().st_size != row["bytes"]:
                    raise SourceError("cached input size mismatch")
                with candidate.open("rb") as cached:
                    return verified(row, cached.read(row["bytes"] + 1))
        except (OSError, SourceError) as exc:
            reason = str(exc) if isinstance(exc, SourceError) else "cache read failed"
    if args.offline:
        raise SourceError(reason)
    for attempt in range(args.retries + 1):
        try:
            return verified(row, mirror_bytes(row, args.timeout))
        except (SourceError, OSError, http.client.HTTPException) as error:
            reason = str(error) if isinstance(error, SourceError) else 'mirror transfer failed'
            if getattr(_connections, 'http', None):
                _connections.http.close()
                _connections.http = None
            if attempt < args.retries:
                time.sleep(min(2, 0.2 * 2**attempt))
    raise SourceError(reason)


def check_space(folder, extra, args):
    if shutil.disk_usage(folder).free < extra + args.reserve_mib * MIB:
        raise Paused("insufficient free space above disk reserve")


def input_bytes(row, args):
    if "prefetch_error" in row.keys():
        raise SourceError(row["prefetch_error"])
    if "prefetched_input" in row.keys():
        # The worker independently checks size, organizer MD5 and the discovery SHA.
        return verified(row, row["prefetched_input"])
    return fetch(row, args)


def source_identities(row, args):
    try:
        data, sha = input_bytes(row, args)
    except SourceError as exc:
        return "discover", str(exc), None, (), ()
    prior = row["committed_discovery"] if "committed_discovery" in row.keys() else None
    if prior and prior[0] == sha:
        # The first path's words and typed forms are already in the shared transaction log.
        return prior[1], prior[2], sha, (), ()
    # Every path is read and verified before reuse. Parsing the same large
    # document again in this worker would produce the same identity evidence.
    cache_key = (row["ext"], sha)
    use_cache = len(data) >= 128 * 1024
    if use_cache and cache_key in _discoveries:
        status, reason, words, forms = _discoveries[cache_key]
        _discoveries.move_to_end(cache_key)
        return status, reason, sha, set(words), list(forms)
    if row["ext"] in NATIVE_TYPES:
        import inspect_native
        handler = inspect_native
        status, reason = "omitted", "native metadata scanned; application streams require a native rewrite"
    else:
        handler = pipeline.HANDLERS[row["ext"]]
        status, reason = "ready", ""
    try:
        names = handler.discover(data)
        words = {pipeline.canonical(word).encode("utf-8", "surrogatepass") for word in names}
        forms = [(kind, name.encode("utf-8", "surrogatepass"), target.encode("utf-8", "surrogatepass"))
                 for kind, name, target in getattr(names, "forms", ())]
    except (ValueError, OSError):
        return "omitted", "invalid or unsupported document structure", sha, (), ()
    if use_cache and len(words) + len(forms) <= 2048:
        size = sum(len(word) + 96 for word in words)
        size += sum(len(kind) + len(raw) + len(target) + 256 for kind, raw, target in forms)
        if size <= 192 * 1024:
            _discoveries[cache_key] = (status, reason, frozenset(words), tuple(forms))
            if len(_discoveries) > 64:
                _discoveries.popitem(last=False)
    return status, reason, sha, words, forms


def row_update(db, row, status, reason=""):
    with db:
        db.execute("UPDATE files SET status=?,reason=? WHERE id=?", (status, reason, row["id"]))


def public_files(directory):
    for root, dirs, files in os.walk(directory, followlinks=False):
        base = Path(root)
        if any((base / name).is_symlink() for name in dirs + files):
            raise ValueError("unexpected symbolic link in staging")
        for name in files:
            yield base / name


def stage_check(db, args, repair=True):
    corpus = args.attempt / "corpus"
    if corpus.is_symlink():
        raise ValueError("invalid corpus staging directory")
    corpus.mkdir(exist_ok=True)
    temp = args.attempt / ".writing"
    temp.unlink(missing_ok=True)
    expected = {r["target"]:r for r in db.execute("SELECT * FROM files WHERE status='staged'")}
    for file in public_files(corpus):
        name = file.relative_to(corpus).as_posix()
        row = expected.pop(name, None)
        if row:
            if digest(file) != row["output_sha"]:
                if not repair:
                    raise Paused("staging changed before delivery; resume to repair it")
                row_update(db, row, "ready")
                file.unlink()
        else:
            done = db.execute("SELECT output_sha FROM files WHERE status='delivered' AND target=?", (name,)).fetchone()
            if not done or digest(file) != done[0]:
                raise ValueError("untracked file in corpus staging")
            file.unlink()
    for row in expected.values():
        if not repair:
            raise Paused("staged file missing before delivery; resume to repair it")
        row_update(db, row, "ready")


def global_inventory(db, key):
    ids = pipeline.Identities(key)
    paths = []
    names = set()
    for path, ext in db.execute("SELECT path,ext FROM files ORDER BY id"):
        path = PurePosixPath(path)
        paths.append(path)
        if ext in pipeline.HANDLERS:
            names.update(pipeline.path_identities(path))
    for word in names:
        ids.learn(word)
    del names
    for (word,) in db.execute("SELECT word FROM identity"):
        ids.learn(word.decode("utf-8", "surrogatepass") if isinstance(word, bytes) else word)
    forms = ((kind, name.decode("utf-8", "surrogatepass"), target.decode("utf-8", "surrogatepass"))
             for kind, name, target in db.execute("SELECT kind,spelling,target FROM pdf_identity_form"))
    pipeline.pdf_identity.resolve(forms, ids, paths)
    return ids, pipeline.PathIndex(paths, ids)


def response_summary(db):
    return dict(db.execute("SELECT status,COUNT(*) FROM files GROUP BY status"))


def marker(db, name):
    return bool(db.execute("SELECT 1 FROM meta WHERE name=?", (name,)).fetchone())


def docs(db, args, index):
    # The per-entry report lists only omitted pseudonyms; reserve its complete
    # encoded size before replacing an earlier local report on a repeated run.
    omitted_estimate = sum(len(index.targets[path].encode()) + len(reason) + 16
                           for path, reason in db.execute("SELECT path,reason FROM files WHERE status='omitted'"))
    check_space(args.attempt, omitted_estimate + 2 * MIB, args)
    code = args.attempt / "code"
    if code.exists():
        shutil.rmtree(code)
    pipeline._write_code(args.attempt)
    for name in ("stream_corpus.py", "STREAMING.md", "check_stream.py"):
        shutil.copyfile(SOURCE / name, code / name)
    # Reports include aggregate verification and pseudonymous omission paths only.
    report = args.attempt / "README.txt"
    with report.open("w", encoding="utf-8") as out:
        out.write("Machina CAD de-identification manifest run\n\n")
        out.write("Use code/STREAMING.md to reproduce. Install dependencies listed in code/.\n")
        out.write(f"Input manifest SHA-256: {digest(args.manifest)}\nInput size ceiling: {args.max_mib} MiB.\n")
        for statement in pipeline.pdf_identity.PATH_REPORTS:
            out.write(statement + "\n")
        total, size, no_md5 = db.execute("SELECT COUNT(*),SUM(bytes),SUM(md5='') FROM files").fetchone()
        out.write(f"Manifest: {total} inputs, {size or 0} bytes, {no_md5 or 0} multipart rows without organizer MD5.\n")
        out.write("Checked byte counts and available organizer MD5s. Checked both passes against the discovery SHA-256.\n")
        for ext, count in db.execute("SELECT ext,COUNT(*) FROM files WHERE status IN ('delivered','staged') GROUP BY ext ORDER BY ext"):
            out.write(f"Emitted {ext}: {count}.\n")
        for reason, count in db.execute("SELECT reason,COUNT(*) FROM files WHERE status='omitted' GROUP BY reason ORDER BY reason"):
            out.write(f"Omitted ({reason}): {count}.\n")
        resolved, unresolved = db.execute("SELECT SUM(resolved),SUM(unresolved) FROM files").fetchone()
        out.write(f"Resolved reference fields: {resolved or 0}. Unresolved or ambiguous: {unresolved or 0}.\n")
        out.write("A referenced file can itself be omitted. Structural checks do not establish complete de-identification.\n")
        out.write("See code/README.md for the format-specific coverage and limitations.\n\nOmitted input entries\n")
        for path, reason in db.execute("SELECT path,reason FROM files WHERE status='omitted' ORDER BY id"):
            out.write(f"{index.targets[path]}  [{reason}]\n")


def local_copy(source, destination, sha):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or digest(destination) != sha:
            raise ValueError("existing archive file differs; use the checkpoint's original archive")
        return
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
        tmp = Path(temp.name)
        try:
            with source.open("rb") as inp:
                shutil.copyfileobj(inp, temp)
            temp.flush()
            os.fsync(temp.fileno())
            if digest(tmp) != sha:
                raise OSError("archive copy failed verification")
            tmp.replace(destination)
            parent_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            tmp.unlink(missing_ok=True)


def drain(db, args, final=False):
    stage_check(db, args, repair=False)
    staged = list(db.execute("SELECT target,output_sha FROM files WHERE status='staged' ORDER BY id"))
    if not args.archive and not args.upload_to:
        if final:
            return
        raise Paused("sanitized chunk ready; supply an archive or official upload destination to release it")
    if args.archive:
        if not marker(db, "destination"):
            if args.archive.exists() and any(args.archive.iterdir()):
                raise ValueError("the archive must initially be empty")
        dest = str(args.archive)
    else:
        dest = args.upload_to
    with db:
        bind(db, "destination", dest)
    if args.archive:
        args.archive.mkdir(parents=True, exist_ok=True)
        for target, sha in staged:
            local_copy(args.attempt / "corpus" / target, args.archive / "corpus" / target, sha)
        if final:
            for f in [args.attempt / "README.txt", *public_files(args.attempt / "code")]:
                local_copy(f, args.archive / f.relative_to(args.attempt), digest(f))
    elif staged or final:
        cmd = [sys.executable, str(args.uploader), "--dest", dest, "--token-file", str(args.token_file)]
        if staged:
            cmd += ["--corpus", str(args.attempt / "corpus")]
        if final:
            cmd += ["--code", str(args.attempt / "code"), "--readme", str(args.attempt / "README.txt")]
        if subprocess.run(cmd).returncode:
            raise Paused("organizer upload did not complete; staged data retained")
    with db:
        db.execute("UPDATE files SET status='delivered' WHERE status='staged'")
        if final:
            bind(db, "completed", "1")
    for target, _ in staged:
        (args.attempt / "corpus" / target).unlink(missing_ok=True)
    # Release directory entries from previous chunks as well as the file contents.
    for root, _, _ in os.walk(args.attempt / "corpus", topdown=False):
        if Path(root) != args.attempt / "corpus":
            with contextlib.suppress(OSError):
                Path(root).rmdir()


def duplicate_discoveries(db):
    """Track repeated manifest entries for the current discovery pass."""
    if not db.execute("SELECT 1 FROM files WHERE status='discover' LIMIT 1").fetchone():
        return {}
    size_limit = 128 * 1024
    return {
        tuple(key): None for key in db.execute(
            "SELECT ext,md5,bytes FROM files WHERE bytes>=? AND md5<>'' "
            "GROUP BY ext,md5,bytes HAVING COUNT(*)>1", (size_limit,)
        )
    }


def prefetch_small_sources(rows, args):
    """Overlap bounded-size mirror transfers with discovery or rewriting."""
    byte_limit = getattr(args, "prefetch_kib", 128) * 1024
    def take(item):
        row, transfer = item
        if transfer is not None:
            try:
                row["prefetched_input"] = transfer.result()[0]
            except SourceError as error:
                row["prefetch_error"] = str(error)
        return row

    pending = deque()
    with ThreadPoolExecutor(max_workers=args.prefetch_small) as pool:
        for row in rows:
            transfer = None
            if row["bytes"] <= byte_limit and (not args.cache or not (args.cache / row["path"]).is_file()):
                transfer = pool.submit(fetch, row, args)
            pending.append((row, transfer))
            if len(pending) >= 2 * args.prefetch_small:
                yield take(pending.popleft())
        while pending:
            yield take(pending.popleft())


def with_discovery_hints(rows, candidates):
    for row in rows:
        entry = dict(row)
        known = candidates.get((row["ext"], row["md5"], row["bytes"]))
        if known:
            entry["committed_discovery"] = known
        yield entry


def phase_rows(db, phase):
    last = 0
    while True:
        batch = list(db.execute("SELECT * FROM files WHERE status=? AND id>? ORDER BY id LIMIT 256", (phase, last)))
        if not batch:
            return
        last = batch[-1]['id']
        yield from batch


def start_writer(index, repeated_sources=()):
    global _writer_index, _repeated_sources, _verified_outputs
    _writer_index = index
    _repeated_sources = repeated_sources
    _verified_outputs = OrderedDict()


def transformed(row, args):
    try:
        data, source_sha = input_bytes(row, args)
    except SourceError as error:
        return 'ready', str(error), None, None
    index = _writer_index
    index.link_counts = Counter()
    source = (row['ext'], source_sha)
    if source in _verified_outputs:
        _verified_outputs.move_to_end(source)
        return 'staged', '', _verified_outputs[source], index.link_counts
    handler = pipeline.HANDLERS[row['ext']]
    try:
        output = handler.rewrite(data, index.ids.alias, index.ids, lambda value: index.reference(row['path'], value))
        for _ in handler.discover(output):
            pass
    except pipeline.drawing_pdf.UnsupportedFont:
        return 'omitted', 'unsupported embedded PDF font subtype', None, None
    except pipeline.drawing_pdf.UnindexedLink:
        return 'omitted', 'unindexed PDF link could not be rewritten', None, None
    except (ValueError, OSError):
        return 'omitted', 'rewrite or structural validation failed', None, None
    if row['ext'] == '.pdf' and args.verify_pdfs:
        from check_native_formats import check_pdf
        try:
            check_pdf(data, output, index.ids, Counter())
        except Exception:
            return 'omitted', 'independent PDF rendering or metadata validation failed', None, None
    # Every source is fetched and hashed, even on a hit. A resolved or missing
    # reference makes the rewrite depend on this input's directory.
    if (source in _repeated_sources and not index.link_counts and len(output) <= 512 * 1024
            and (row['ext'] != '.pdf' or args.verify_pdfs)):
        _verified_outputs[source] = output
        if len(_verified_outputs) > 64:
            _verified_outputs.popitem(last=False)
    return 'staged', '', output, index.link_counts


def process_rows(rows, args, operation, index=None, repeated_sources=()):
    # Native PDF and system frameworks on macOS require fresh interpreter workers.
    # On Linux, workers share the immutable inventory by copy-on-write.
    # Prefetch starts network threads in the parent. Forking would inherit TLS locks.
    threaded = getattr(args, 'prefetch_small', 0) and not args.offline
    context = multiprocessing.get_context('spawn' if sys.platform == 'darwin' or threaded else 'fork')
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=context, initializer=start_writer,
                             initargs=(index, repeated_sources)) as pool:
        pending = {pool.submit(operation, dict(row), args): row for row in itertools.islice(rows, args.workers)}
        while pending:
            complete, _ = wait(pending, return_when=FIRST_COMPLETED)
            for job in complete:
                row = pending.pop(job)
                yield row, job.result()
                next_row = next(rows, None)
                if next_row is not None:
                    pending[pool.submit(operation, dict(next_row), args)] = next_row


def work(db, args, key):
    stage_check(db, args)
    if marker(db, "completed"):
        return
    destination = db.execute("SELECT value FROM meta WHERE name='destination'").fetchone()
    requested = str(args.archive) if args.archive else args.upload_to
    if destination and destination[0] != requested:
        raise ValueError("resume with the same archive or upload destination")
    remaining = args.work_limit
    chunk = args.chunk_mib * MIB
    occupied = db.execute("SELECT COALESCE(SUM(output_bytes),0) FROM files WHERE status='staged'").fetchone()[0]
    for phase in ("discover", "ready"):
        ids = index = None
        if phase == "ready":
            ids, index = global_inventory(db, key)
        discovery = phase == 'discover'
        operation = source_identities if discovery else transformed
        rows = itertools.islice(phase_rows(db, phase), remaining)
        repeated_sources = ()
        if discovery:
            candidates = duplicate_discoveries(db)
            rows = with_discovery_hints(rows, candidates)
        else:
            repeated_sources = {tuple(row) for row in db.execute(
                "SELECT ext, source_sha FROM files WHERE status='ready' AND source_sha IS NOT NULL "
                "GROUP BY ext, source_sha HAVING COUNT(*) > 1"
            )}
        if getattr(args, 'prefetch_small', 0) and not args.offline:
            rows = prefetch_small_sources(rows if discovery else map(dict, rows), args)
        for row, result in process_rows(rows, args, operation, index, repeated_sources):
            if remaining is not None:
                remaining -= 1
            if discovery:
                status, reason, sha, words, forms = result
                check_space(args.state, MIB, args)
                with db:
                    db.executemany("INSERT OR IGNORE INTO identity VALUES (?)", ((word,) for word in words))
                    db.executemany("INSERT OR IGNORE INTO pdf_identity_form VALUES (?,?,?)", forms)
                    db.execute("UPDATE files SET status=?,reason=?,source_sha=? WHERE id=?", (status, reason, sha, row['id']))
                identity_key = (row["ext"], row["md5"], row["bytes"])
                if sha and identity_key in candidates:
                    candidates[identity_key] = (sha, status, reason)
                continue
            status, reason, output, counts = result
            if status != 'staged':
                row_update(db, row, status, reason)
                continue
            if len(output) > chunk:
                raise Paused("one sanitized file exceeds the staging cap; increase --chunk-mib")
            if occupied + len(output) > chunk:
                drain(db, args)
                occupied = 0
            check_space(args.attempt, len(output), args)
            check_space(args.state, 0, args)
            target = index.targets[row["path"]]
            file = args.attempt / "corpus" / target
            file.parent.mkdir(parents=True, exist_ok=True)
            with db:
                db.execute("""UPDATE files SET status='staged',reason='',target=?,output_bytes=?,output_sha=?,
                    resolved=?,unresolved=? WHERE id=?""",
                    (target, len(output), hashlib.sha256(output).hexdigest(), counts["resolved in input inventory"],
                     counts["unresolved external or ambiguous"], row["id"]))
            temporary = args.attempt / ".writing"
            with temporary.open("wb") as stream:
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(file)
            occupied += len(output)
        if remaining == 0:
            raise Paused('work limit reached')
        if db.execute("SELECT 1 FROM files WHERE status=? LIMIT 1", (phase,)).fetchone():
            raise Paused(f"{phase} has unavailable or unverifiable inputs; correct the source and resume")
    docs(db, args, index)
    drain(db, args, final=True)


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefetch-small", type=int, default=0, metavar="N",
                        help="overlap N verified mirror reads for small discovery and rewrite inputs")
    parser.add_argument("--prefetch-kib", type=int, default=128, metavar="KIB",
                        help="largest input read ahead from the mirror (default: 128 KiB)")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path, help="private SQLite checkpoint and default HMAC key")
    parser.add_argument("--attempt", required=True, type=Path, help="new numbered attempt-N staging folder")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--cache", type=Path, help="optional read-only local organizer input tree")
    parser.add_argument("--offline", action="store_true", help="use the local cache exclusively")
    parser.add_argument("--learn-native", action="store_true", help="learn typed identities from opaque native files and report their omission")
    parser.add_argument("--verify-pdfs", action="store_true", help="apply independent PDF reader checks before staging any PDF")
    parser.add_argument("--max-mib", type=int, default=64, help="input ceiling per handler (fixed for this checkpoint)")
    parser.add_argument("--chunk-mib", type=int, default=256, help="maximum sanitized corpus bytes staged locally")
    parser.add_argument("--state-mib", type=int, default=1024, help="maximum SQLite database size")
    parser.add_argument("--reserve-mib", type=int, default=256, help="minimum free space for staging and state")
    parser.add_argument("--workers", type=int, default=4, help="parallel source reads; into memory only")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--work-limit", type=int, help="stop after this many source fetches across both passes")
    dest = parser.add_mutually_exclusive_group()
    dest.add_argument("--archive", type=Path, help="initially empty durable destination on a sufficiently large volume")
    dest.add_argument("--upload-to", help="exact team Builders gs:// destination copied from the track page")
    parser.add_argument("--uploader", type=Path, help="official organizer upload.py")
    parser.add_argument("--token-file", type=Path, help="operator-owned renewal file passed to official upload.py")
    args = parser.parse_args(argv)
    for name in ("manifest", "state", "attempt", "cache", "key_file", "archive", "uploader", "token_file"):
        path = getattr(args, name)
        if path is not None:
            setattr(args, name, path.expanduser().resolve())
    args.key_file = args.key_file or args.state / "alias.key"
    if (min(args.workers, args.max_mib, args.chunk_mib, args.state_mib, args.timeout, args.prefetch_kib) < 1
            or min(args.retries, args.reserve_mib, args.prefetch_small) < 0 or (args.work_limit is not None and args.work_limit < 1)):
        parser.error("invalid worker, size, timeout, retry or work limit")
    if not re.fullmatch(r"attempt-[1-9]\d*", args.attempt.name) or (args.offline and not args.cache):
        parser.error("use an official attempt-N directory and provide a cache when offline")
    roots = [p for p in (args.state, args.attempt, args.cache, args.archive, SOURCE) if p]
    if any(x.is_relative_to(y) or y.is_relative_to(x) for x, y in itertools.combinations(roots, 2)):
        parser.error("state, staging, cache, archive and source must be disjoint directories")
    private = [p for p in (args.key_file, args.manifest, args.token_file) if p]
    if any(p.is_relative_to(d) for p in private for d in [args.attempt, SOURCE] + ([args.archive] if args.archive else [])):
        parser.error("keep the key, token and manifest outside all deliverables and code")
    if args.upload_to:
        pattern = r"gs://builders-machina-submissions-use1/teams/[A-Za-z0-9_-]+/" + args.attempt.name + "/"
        if not re.fullmatch(pattern, args.upload_to):
            parser.error("use the team's exact official folder for this numbered attempt, ending in /")
        if not args.uploader or not args.token_file or digest(args.uploader) != UPLOADER_SHA:
            parser.error("supply the published official upload.py and your renewal-file path")
        if not os.environ.get("MACHINA_UPLOAD_TOKEN"):
            parser.error("set MACHINA_UPLOAD_TOKEN for the official uploader's initial authentication")
    elif args.token_file or args.uploader:
        parser.error("uploader arguments require an explicit --upload-to")
    return args


def run(args):
    os.umask(0o077)
    if args.verify_pdfs:
        import check_native_formats  # Fail before scanning if the required native readers are missing.
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.attempt.mkdir(parents=True, exist_ok=True)
    args.key_file.parent.mkdir(parents=True, exist_ok=True)
    with (args.state / "run.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Paused("another process owns this checkpoint")
        check_space(args.state, 0, args)
        key = pipeline._load_key(args.key_file)
        with contextlib.closing(sqlite3.connect(args.state / "checkpoint.sqlite3")) as db:
            db.row_factory = sqlite3.Row
            # SQLite's page cap bounds the persistent DB. Its rollback journal
            # can temporarily require up to one extra database of scratch space.
            page = db.execute("PRAGMA page_size").fetchone()[0]
            max_pages = args.state_mib * MIB // page
            if db.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0] > max_pages:
                raise Paused("existing checkpoint exceeds --state-mib")
            db.execute("PRAGMA temp_store=MEMORY")
            init_db(db, args, key)
            try:
                work(db, args, key)
            finally:
                print(json.dumps(response_summary(db), sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        run(options())
    except Paused as error:
        sys.exit(f"Paused: {error}")
    except (ValueError, OSError, sqlite3.Error) as error:
        sys.exit(f"Failed: {error}")
