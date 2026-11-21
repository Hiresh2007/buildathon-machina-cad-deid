"""Hold back PDFs that fail independent engineering or metadata checks before submission."""

import argparse
from collections import Counter
import hashlib
import logging
from pathlib import Path
import re
import shutil
import tempfile

from check_native_formats import check_pdf
from pipeline import Identities, PathIndex


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').digest()


def screen(source, attempt, hold, key):
    source, attempt, hold = (p.resolve() for p in (source, attempt, hold))
    if any(a == b or a in b.parents or b in a.parents for a, b in
           ((source, attempt), (hold, source), (hold, attempt))):
        raise ValueError('use separate private input, attempt and holdback directories')
    hold.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = attempt / 'README.txt'
    baseline = hold / 'original-report.txt'
    if not baseline.exists():
        shutil.copyfile(report, baseline)
    original = baseline.read_text()
    ids = Identities(key.read_bytes())
    paths = sorted(p.relative_to(source) for p in source.rglob('*') if p.is_file() or p.is_symlink())
    index = PathIndex(paths, ids)
    failures = []
    counts = Counter()
    for path in paths:
        if path.suffix.lower() != '.pdf':
            continue
        target = index.targets[str(path)]
        output = attempt / 'corpus' / target
        held = hold / 'documents' / target
        if held.is_file() and not output.exists():
            failures.append(target)
            continue
        if not output.is_file():
            continue
        try:
            file_counts = Counter()
            check_pdf((source / path).read_bytes(), output.read_bytes(), ids, file_counts)
            counts.update(file_counts)
            counts['PDFs passed'] += 1
        except Exception:
            # A verifier error also leaves the copy out of the submitted corpus.
            held.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if held.exists() and digest(held) != digest(output):
                raise ValueError('held PDF and current output differ')
            shutil.move(str(output), str(held))
            failures.append(target)
    total = sum(p.is_file() for p in (attempt / 'corpus').rglob('*'))
    text = re.sub(r'(Input files observed: \d+\. Output files:) \d+\.', rf'\g<1> {total}.', original)
    text = re.sub(r'Written \.pdf: \d+\.', f"Written .pdf: {counts['PDFs passed']}.", text)
    text += f'\nIndependent PDF verification passed for {counts["PDFs passed"]} outputs. '
    text += f'An additional {len(failures)} PDFs were held back after rendering, geometry or metadata verification.\n'
    text += 'Reproduce this screen on Python 3.12 with code/requirements-verify.txt, then run:\n'
    text += 'python3 code/screen_pdfs.py --input /private/practice --attempt /private/reproduction/attempt-1 --key-file /private/key.bin --holdback /private/pdf-holdback\n'
    text += '\n'.join(f'{name}  [independent PDF verification]' for name in failures) + '\n'
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=attempt, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(report)
    print(f'PDFs passed {counts["PDFs passed"]}. Held back {len(failures)}. Corpus files {total}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'attempt', 'key-file', 'holdback'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    screen(args.input, args.attempt, args.holdback, args.key_file)
