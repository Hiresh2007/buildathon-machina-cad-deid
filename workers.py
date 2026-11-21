"""Run independent files in input order and share read-only CAD indexes per worker."""

import multiprocessing
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor


def _initialize(operation, state):
    global _operation, _state
    _operation, _state = operation, state


def _process(item):
    return _operation(_state, item)


def worker_context():
    # On macOS a native framework initialized in another parent thread cannot
    # be safely used after fork. Share indexes by copy-on-write where supported.
    methods = multiprocessing.get_all_start_methods()
    method = "fork" if sys.platform != "darwin" and "fork" in methods else "spawn"
    return multiprocessing.get_context(method)


def ordered_map(operation, state, items, workers):
    if workers == 1:
        for item in items:
            yield operation(state, item)
        return
    context = worker_context()
    with ProcessPoolExecutor(workers, mp_context=context, initializer=_initialize,
                             initargs=(operation, state)) as pool:
        yield from pool.map(_process, items, chunksize=2)


def discover(state, path):
    from pipeline import HANDLERS

    root, limit = state
    source = root / path
    extension = path.suffix.lower()
    try:
        if source.is_symlink():
            return path, (), "symbolic link"
        if extension not in HANDLERS:
            return path, (), "unsupported format"
        if source.stat().st_size > limit:
            return path, (), "over configured file-size limit"
        found = HANDLERS[extension].discover(source.read_bytes())
        identities = found if hasattr(found, "forms") else tuple(dict.fromkeys(found))
        return path, identities, None
    except (ValueError, OSError):
        return path, (), "invalid, unreadable or unsupported document structure"


def rewrite(state, path):
    from pipeline import HANDLERS

    root, corpus, index = state
    index.link_counts = Counter()
    name = str(path)
    handler = HANDLERS[path.suffix.lower()]
    try:
        result = handler.rewrite((root / path).read_bytes(), index.ids.alias, index.ids,
                                 lambda value: index.reference(name, value))
        for _ in handler.discover(result):
            pass
    except (ValueError, OSError):
        return path, {}, "rewrite or validation failed"
    out = corpus / index.targets[name]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(result)
    return path, index.link_counts, None
