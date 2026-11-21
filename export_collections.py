"""Export completed source collections from a verified drawing archive."""
import argparse
import contextlib
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile

import pipeline
from stream_corpus import digest, local_copy


def selection(checkpoint, through):
    with contextlib.closing(sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        metadata = dict(db.execute("SELECT name,value FROM meta"))
        rows = db.execute(
            "SELECT id,path,ext,status,reason,target,output_bytes,output_sha FROM files ORDER BY id"
        ).fetchall()
    if not rows or not 1 <= through <= len(rows) or metadata.get("inventory") != "complete":
        raise ValueError("the checkpoint must contain the complete input inventory and the selected row")
    frozen = json.loads(metadata["source version"])
    source = Path(__file__).resolve().parent
    if any(digest(source / name) != expected for name, expected in frozen.items()):
        raise ValueError("the checkpoint was produced with different transformation code")
    groups = defaultdict(list)
    for row in rows:
        groups[PurePosixPath(row["path"]).parts[:3]].append(row)
    complete = [members for members in groups.values() if members[-1]["id"] <= through]
    selected = [row for members in complete for row in members]
    if not selected or any(row["status"] not in {"delivered", "omitted"} for row in selected):
        raise ValueError("the selected source collections still have unfinished files")
    outputs = [row for row in selected if row["status"] == "delivered"]
    if not outputs:
        raise ValueError("none of the selected files has been delivered")
    report = {"through": through, "manifest": metadata["manifest"], "inventory": len(rows),
              "collections": len(complete), "selected": len(selected), "files": len(outputs),
              "bytes": sum(row["output_bytes"] for row in outputs),
              "types": Counter(row["ext"] for row in outputs),
              "omissions": Counter(row["reason"] for row in selected if row["status"] == "omitted")}
    return outputs, report


def write_package(folder, report):
    pipeline._write_code(folder)
    shutil.copyfile(__file__, folder / "code" / "export_collections.py")
    text = [
        "Machina CAD De-ID drawing collection practice",
        "",
        f"Drawing manifest SHA-256: {report['manifest']}",
        f"Identity and reference inventory: {report['inventory']} manifest entries.",
        f"Selection: whole source collections whose final row is at or below {report['through']}.",
        "A source collection is identified by the first three path components.",
        f"Selected {report['selected']} inputs in {report['collections']} collections.",
        f"Delivered {report['files']} sanitized files, totalling {report['bytes']} bytes.",
        "All emitted PDFs passed independent rendering, geometry and metadata checks.",
        "References were resolved against the full inventory. Targets in other collections may be outside this practice scope.",
        "", "Output counts by format",
        *(f"{ext}: {n}" for ext, n in sorted(report["types"].items())),
        "", "Selected inputs omitted by the native processors",
        *(f"{reason}: {n}" for reason, n in sorted(report["omissions"].items())),
        "", "Reproduction",
        "Use Python 3.12 with code/requirements.txt and code/requirements-verify.txt.",
        "Keep the private HMAC key, checkpoint, input, archive and fresh export in separate locations.",
        "python3 code/select_drawings.py manifest.tsv drawings.tsv",
        "python3 code/stream_corpus.py --manifest drawings.tsv --state /private/state --attempt /private/stage/attempt-2 "
        "--key-file /private/keys/alias.bin --archive /private/archive --learn-native --verify-pdfs --max-mib 128",
        f"python3 code/export_collections.py --checkpoint /private/state/checkpoint.sqlite3 --archive /private/archive "
        f"--through-row {report['through']} --output /private/export/drawings",
    ]
    (folder / "README.txt").write_text("\n".join(text) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--through-row", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = (args.archive / "corpus").resolve()
    output = args.output.resolve()
    if not corpus.is_dir() or output.exists() or output.is_relative_to(corpus) or corpus.is_relative_to(output):
        parser.error("provide a completed archive and a separate new output directory")
    rows, report = selection(args.checkpoint, args.through_row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp) / "attempt"
        stage.mkdir()
        for row in rows:
            path = PurePosixPath(row["target"])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("invalid archived target")
            source = (corpus / path).resolve()
            if not source.is_relative_to(corpus) or source.stat().st_size != row["output_bytes"]:
                raise ValueError("archived output is outside the corpus or has an unexpected size")
            local_copy(source, stage / "corpus" / path, row["output_sha"])
        write_package(stage, report)
        stage.rename(output)


if __name__ == "__main__":
    main()
