"""Reproduce the eight-format practice input from the official manifest and a private mirror."""

import argparse
from collections import defaultdict
import hashlib
from pathlib import Path, PurePosixPath
import random

from stream_corpus import manifest_rows


LIMITS = {".step": 1600, ".stp": 450, ".dxf": 320, ".pdf": 600, ".stl": 800,
          ".sat": 1050, ".svg": 550, ".fcstd": 45}


def selected(manifest):
    eligible = defaultdict(list)
    for path, size, checksum, _, ext in manifest_rows(manifest):
        if ext in LIMITS and 96 <= size < 2_500_000 and len(path) <= 768:
            eligible[ext].append((path, size, checksum))
    generator = random.Random(752913)
    for extension, limit in LIMITS.items():
        options = eligible[extension]
        generator.shuffle(options)
        neighbors = defaultdict(list)
        for entry in options:
            neighbors[PurePosixPath(entry[0]).parent].append(entry)
        chosen = set()
        for entry in options:
            for row in (entry, *neighbors[PurePosixPath(entry[0]).parent][:2]):
                if row[0] in chosen:
                    continue
                chosen.add(row[0])
                yield row
                if len(chosen) == limit:
                    break
            if len(chosen) == limit:
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="private organizer file tree")
    parser.add_argument("--output", type=Path, required=True, help="new private directory")
    opts = parser.parse_args()
    source, output = opts.input.resolve(), opts.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        parser.error("use disjoint source and output directories")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    total = 0
    for name, size, checksum in selected(opts.manifest):
        file = source / name
        if file.is_symlink() or not file.resolve().is_relative_to(source):
            parser.error("the private source contains a link outside the input tree")
        content = file.read_bytes()
        if len(content) != size or (checksum and hashlib.md5(content).hexdigest() != checksum):
            parser.error("source differs from the organizer manifest")
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open('xb') as handle:
            handle.write(content)
        total += 1
    print(f"Prepared {total} practice inputs.")


if __name__ == "__main__":
    main()
