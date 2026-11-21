"""Check private text DXF pairs with ezdxf and compare preserved drawing records."""

import argparse
from collections import Counter
from pathlib import Path

import ezdxf

import dxf
import pdf_identity
from pipeline import Identities, PathIndex


def check(root, attempt, key_file):
    paths = [p.relative_to(root) for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    ids = Identities(key_file.read_bytes())
    pdf_identity.restore_directory_forms(root, attempt, paths, ids)
    index = PathIndex(paths, ids)
    opened = entities = defects = failures = 0
    for item in paths:
        if item.suffix.lower() != ".dxf":
            continue
        source = root / item
        dest = attempt / "corpus" / index.targets[str(item)]
        if not dest.is_file():
            failures += 1
            continue
        try:
            left = ezdxf.readfile(source)
        except ezdxf.DXFError as original:
            # Check the same native limitation explicitly, with no original identifiers in the report.
            try:
                ezdxf.readfile(dest)
            except ezdxf.DXFError as transformed:
                if type(original) is type(transformed) and str(original) == str(transformed):
                    defects += 1
                    continue
            failures += 1
            continue
        try:
            right = ezdxf.readfile(dest)
        except ezdxf.DXFError:
            failures += 1
            continue
        before = Counter(entity.dxftype() for entity in left.modelspace())
        after = Counter(entity.dxftype() for entity in right.modelspace())
        _, codes_a, values_a, _ = dxf._pairs(source.read_bytes())
        _, codes_b, values_b, _ = dxf._pairs(dest.read_bytes())
        untouched_equal = all(a == b for kind, a, b in zip(codes_a, values_a, values_b)
                              if kind not in {1, 3, 999})
        if before == after and codes_a == codes_b and untouched_equal:
            opened += 1
            entities += sum(before.values())
        else:
            failures += 1
    print(f"DXF pairs reopened: {opened}. Matching modelspace entities: {entities}. "
          f"Pairs with identical native source errors: {defects}. Missing or mismatched: {failures}.")
    return 0 if opened and failures == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--attempt", required=True, type=Path)
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(check(args.input, args.attempt, args.key_file))
