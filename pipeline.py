"""Build a private Machina CAD De-ID attempt from an input directory."""

import argparse
import hashlib
import hmac
import os
import posixpath
import re
import secrets
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import step
import dxf
import mesh
import drawing_pdf
import fcstd
import manufacturing
import sat
import svg
import workers as file_workers
from naming import canonical, structural, DIMENSION


HANDLERS = {".step": step, ".stp": step, ".dxf": dxf, ".stl": mesh, ".pdf": drawing_pdf,
            ".sat": sat, ".svg": svg, ".fcstd": fcstd, ".3mf": manufacturing}
CONTACT = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?:https?://|www\.)[^\s<>\x22']+"
    r"|(?<!\w)\+\d{1,3}[- (][0-9() .-]{6,18}\d(?!\w)", re.I,
)


class Identities:
    def __init__(self, key):
        self.key = key
        self.words = {}

    def alias(self, value):
        name = canonical(value)
        # Some native metadata readers return unpaired UTF-16 surrogates. Preserve
        # their code points for distinct, repeatable aliases on damaged inputs.
        digest = hmac.new(self.key, name.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:20]
        if re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", name):
            return "contact-" + digest + "@example.invalid"
        if name.startswith(("http://", "https://", "www.")):
            return "https://site-" + digest + ".example.invalid"
        dimensions = [m[0].replace("µ", "u").replace("×", "x") for m in DIMENSION.finditer(name)]
        return " ".join(["Anon-" + digest, *dimensions])

    def learn(self, value):
        word = canonical(value)
        if len(word) < 3 or not any(c.isalpha() for c in word) or structural(word) or word in step.NEUTRAL:
            return
        node = self.words
        for char in word:
            node = node.setdefault(char, {})
        node[None] = self.alias(value)

    def matches(self, text):
        contacts = {match.start(): match for match in CONTACT.finditer(text)}
        i = 0
        while i < len(text):
            end, j, node, replacement = i, i, self.words, None
            if i in contacts:
                match = contacts[i]
                end, replacement = match.end(), self.alias(match[0])
            elif i == 0 or not (text[i - 1].isalnum() and text[i].isalnum()):
                while j < len(text):
                    char = unicodedata.normalize("NFKC", text[j]).casefold()
                    if char.isspace():
                        char = " "
                        while j + 1 < len(text) and text[j + 1].isspace():
                            j += 1
                    for part in char:
                        node = node.get(part)
                        if node is None:
                            break
                    if node is None:
                        break
                    j += 1
                    if None in node and (j == len(text) or not (text[j - 1].isalnum() and text[j].isalnum())):
                        end, replacement = j, node[None]
            if replacement is None:
                i += 1
            else:
                yield i, end, replacement
                i = end

    def redact_text(self, text):
        result, cursor = [], 0
        for start, end, replacement in self.matches(text):
            result.extend((text[cursor:start], replacement))
            cursor = end
        result.append(text[cursor:])
        return "".join(result)

    __call__ = redact_text


class PathIndex:
    def __init__(self, paths, ids):
        self.ids = ids
        self.targets = {".": "."}
        self.by_name = defaultdict(list)
        self.by_relative = defaultdict(list)
        self.link_counts = Counter()
        taken = defaultdict(set)
        for path in sorted(paths):
            parent = PurePosixPath(".")
            for n, part in enumerate(path.parts):
                child = parent / part
                if str(child) not in self.targets:
                    directory = n < len(path.parts) - 1
                    proposed = self.part(part, directory)
                    used = taken[self.targets[str(parent)]]
                    if proposed.casefold() in used:
                        suffix = "" if directory else PurePosixPath(proposed).suffix
                        unique = hmac.new(ids.key, str(child).encode(), hashlib.sha256).hexdigest()[:16]
                        proposed = "Entry-" + unique + suffix
                    if proposed.casefold() in used:
                        raise ValueError("output path collision")
                    used.add(proposed.casefold())
                    self.targets[str(child)] = posixpath.join(self.targets[str(parent)], proposed).removeprefix("./")
                parent = child
            self.by_name[canonical(path.name)].append(str(path))
            self.by_relative[canonical(path.as_posix())].append(str(path))

    def part(self, value, directory):
        suffix = "" if directory else PurePosixPath(value).suffix
        stem = value[:-len(suffix)] if suffix else value
        return (stem if structural(stem) else self.ids.alias(stem)) + suffix

    def reference(self, current, value):
        clean = value.strip().replace("\\", "/").removeprefix("file://")
        if not clean:
            return value
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(current), clean))
        if candidate not in self.targets:
            matches = self.by_relative.get(canonical(candidate), [])
            if len(matches) == 1:
                candidate = matches[0]
        if candidate not in self.targets:
            names = self.by_name.get(canonical(posixpath.basename(clean)), [])
            candidate = names[0] if len(names) == 1 else None
        if candidate in self.targets:
            self.link_counts["resolved in input inventory"] += 1
            return posixpath.relpath(self.targets[candidate], posixpath.dirname(self.targets[current]) or ".")
        self.link_counts["unresolved external or ambiguous"] += 1
        # An unavailable external file has no verified destination. Mask its basename.
        base = posixpath.basename(clean) or clean
        return self.part(base, False)


def _within(path, folder):
    return path == folder or folder in path.parents


def _load_key(path):
    if not path.exists():
        try:
            with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as out:
                out.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("the key file must contain exactly 32 bytes")
    return key


def _write_code(staged):
    source = Path(__file__).resolve().parent
    dest = staged / "code"
    dest.mkdir()
    names = (
        "pipeline.py", "workers.py", "step.py", "dxf.py", "naming.py", "mesh.py", "drawing_pdf.py",
        "sat.py", "svg.py", "fcstd.py", "manufacturing.py", "check_manufacturing.py", "requirements.txt", "requirements-verify.txt", "check.py",
        "check_formats.py", "check_native_formats.py", "check_exchange.py", "check_geometry.py",
        "check_dxf.py", "stream_corpus.py", "select_practice.py", "screen_pdfs.py", "check_stream.py", "inspect_native.py", "NATIVE-AUDIT.md",
        "STREAMING.md", "README.md", "LICENSE",
    )
    for name in names:
        if (source / name).is_file():
            shutil.copyfile(source / name, dest / name)


def run(input_dir, attempt, key_file, limit, workers=1):
    root, attempt, key_file = (p.resolve() for p in (input_dir, attempt, key_file))
    code = Path(__file__).resolve().parent
    if not root.is_dir() or not re.fullmatch(r"attempt-[1-9]\d*", attempt.name):
        raise ValueError("provide an input directory and a new attempt-N output path")
    if attempt.exists() or _within(attempt, root) or _within(root, attempt):
        raise ValueError("input and new attempt must be separate directories")
    if _within(key_file, root) or _within(key_file, attempt) or any(_within(p, code) for p in (key_file, root, attempt)):
        raise ValueError("keep the key, inputs and output separate from each other and the code")
    key_file.parent.mkdir(parents=True, exist_ok=True)
    ids = Identities(_load_key(key_file))
    # Include symlinks in the audit, but never follow a file link outside the input.
    paths = [p.relative_to(root) for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    if any(path.suffix.lower() == ".pdf" for path in paths):
        drawing_pdf.library()
    index = PathIndex(paths, ids)
    for path in paths:
        if path.suffix.lower() in HANDLERS:
            ids.learn(path.stem)
            for part in path.parts[:-1]:
                ids.learn(part)
    counts, omitted, accepted = Counter(), [], []
    for path, names, reason in file_workers.ordered_map(file_workers.discover, (root, limit), sorted(paths), workers):
        if reason:
            counts[reason] += 1
            omitted.append((index.targets[str(path)], reason))
        else:
            for name in names:
                ids.learn(name)
            accepted.append(path)

    attempt.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=attempt.parent, prefix=".machina-") as tmp:
        staged = Path(tmp) / attempt.name
        corpus = staged / "corpus"
        corpus.mkdir(parents=True)
        written = Counter()
        links = Counter()
        for path, references, reason in file_workers.ordered_map(file_workers.rewrite, (root, corpus, index), accepted, workers):
            if reason:
                counts[reason] += 1
                omitted.append((index.targets[str(path)], reason))
            else:
                links.update(references)
                written[path.suffix.lower()] += 1
        index.link_counts = links
        _write_code(staged)
        report = [
            "Machina CAD De-ID", "",
            "Reproduce on Python 3.12: install PDF support with python3 -m pip install -r code/requirements.txt.",
            "python3 code/pipeline.py --input /private/input --attempt /private/new/attempt-1 --key-file /private/key.bin",
            f"This run used --max-mib {limit // (1024 * 1024)} --workers {workers}. Supply the same 32-byte private key and input snapshot.",
            "The key and private identity dictionary are excluded. See code/README.md for field coverage.",
            "", f"Input files observed: {len(paths)}. Output files: {sum(written.values())}.",
        ]
        report += [f"Written {ext}: {count}." for ext, count in sorted(written.items())]
        report += [f"Omitted ({reason}): {count}." for reason, count in sorted(counts.items())]
        report += [f"Path fields {label}: {count}." for label, count in sorted(index.link_counts.items())]
        report += ["", "Scope and limitations",
                   "Only the supplied input directory was examined. Supported files are text STEP/STP, ASCII DXF, STL meshes, PDFs, ACIS SAT, SVG, FreeCAD FCStd and standard 3MF core packages.",
                   "Other formats, archives, previews, logos, ambiguous references and unrecognized free text require additional processing.",
                   "Embedded data and unsupported structures may carry identifiers. Local validation is structural; manual review is required.",
                   "References resolve against the input inventory. A target missing from the emitted corpus remains unavailable.",
                   "", "Omitted input entries (pseudonymous paths)"]
        report += [f"{path}  [{reason}]" for path, reason in omitted]
        (staged / "README.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
        staged.rename(attempt)
    print(f"Processed {sum(written.values())}/{len(paths)} files. Omitted {len(omitted)}. See attempt README.txt.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--attempt", required=True, type=Path)
    parser.add_argument("--key-file", required=True, type=Path,
                        help="private 32-byte key; generated on first use")
    parser.add_argument("--max-mib", type=int, default=64, help="per-file memory guard in MiB")
    parser.add_argument("--workers", type=int, default=1, help="parallel files per pass")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.max_mib < 1:
        parser.error("--max-mib must be positive")
    try:
        run(args.input, args.attempt, args.key_file, args.max_mib * 1024 * 1024, args.workers)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Attempt failed: {error}\n")


if __name__ == "__main__":
    main()
