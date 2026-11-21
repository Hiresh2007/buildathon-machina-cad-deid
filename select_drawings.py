"""Select an entire PDF collection and the related engineering models from a Machina manifest."""
import argparse
from collections import Counter
from pathlib import Path, PurePosixPath

from naming import canonical
from stream_corpus import manifest_rows, NATIVE_TYPES

MODELS = {'.step', '.stp', '.sat', '.stl', '.fcstd', '.svg', '.3mf', '.dxf', '.wrl'}

def select(manifest, output):
    names, directories = set(), set()
    for name, _, _, _, extension in manifest_rows(manifest):
        if extension == '.pdf':
            path = PurePosixPath(name)
            names.add(canonical(path.stem))
            directories.add(path.parent)
    counts = Counter()
    with output.open('x', encoding='utf-8', newline='\n') as out:
        out.write('# Drawing cohort. All PDFs, same-stem models and native files, plus neighboring exchange models.\n')
        for name, size, md5, url, extension in manifest_rows(manifest):
            path = PurePosixPath(name)
            same_name = canonical(path.stem) in names
            if (extension == '.pdf' or (extension in MODELS and (same_name or path.parent in directories))
                    or (extension in NATIVE_TYPES and same_name)):
                out.write(f'{name}\t{size}\t{md5}\t{url}\n')
                counts[extension] += 1
    return counts

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    for extension, count in sorted(select(args.manifest, args.output).items()):
        print(extension, count)
