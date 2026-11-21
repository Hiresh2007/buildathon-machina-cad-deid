"""Synthetic regression for Part 21 quoting, identity consistency and external references."""

import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory

import step
from pipeline import Identities, PathIndex, run


def document(name, link):
    return f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('practice data'), '2;1');
FILE_NAME('{name}','2026-01-01T00:00:00',('Rae O''Neil'),('Fictional Quartz Ltd.'),'writer','CAD','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));
ENDSEC;
DATA;
/* Fictional Quartz Ltd. #999; */
#1=APPLICATION_CONTEXT('mechanical');
#2=PRODUCT_CONTEXT('',#1,'mechanical');
#3=PRODUCT('CASE-080','CASE-080','25 mm',(#2));
#4=CARTESIAN_POINT('CASE-080 fixture',(1.25,-2.0,0.));
#5=EXTERNAL_SOURCE(IDENTIFIER('{link}'));
#6=DESCRIPTIVE_REPRESENTATION_ITEM('Author','Rae O''Neil');
#7=DESCRIPTIVE_REPRESENTATION_ITEM('material','AISI 304');
ENDSEC;
END-ISO-10303-21;
""".encode()


def check():
    with TemporaryDirectory() as tmp:
        private = Path(tmp)
        root = private / "input"
        a = Path("sources/github/drawings/CASE-080.step")
        b = Path("sources/github/assemblies/MODEL-19.stp")
        for here, there in ((a, b), (b, a)):
            source = root / here
            source.parent.mkdir(parents=True)
            source.write_bytes(document(here.name, f"../{there.parent.name}/{there.name}"))
        drawing = Path("sources/gitlab/drawings/CASE-080.dxf")
        (root / drawing).parent.mkdir(parents=True)
        (root / drawing).write_bytes(b"0\nSECTION\n2\nHEADER\n9\n$LASTSAVEDBY\n1\nRae O'Neil\n0\nENDSEC\n0\nEOF\n")
        (root / "unhandled.bin").write_bytes(b"cannot process")
        (root / "broken.stp").write_bytes(document("broken.stp", a.name).replace(b"(#2)", b"(#9999)"))
        key = private / "key"
        first, repeat = private / "attempt-1", private / "attempt-2"
        with contextlib.redirect_stdout(io.StringIO()):
            run(root, first, key, 1 << 20)
            run(root, repeat, key, 1 << 20)
        ids = Identities(key.read_bytes())
        paths = [p.relative_to(root) for p in root.rglob("*") if p.is_file()]
        index = PathIndex(paths, ids)
        for here, there in ((a, b), (b, a)):
            output = first / "corpus" / index.targets[str(here)]
            text = output.read_bytes()
            assert text == (repeat / "corpus" / index.targets[str(here)]).read_bytes()
            assert b"CASE-080" not in text and b"Fictional Quartz" not in text and b"O''Neil" not in text
            assert text.count(ids.alias("Rae O'Neil").encode()) == 2
            assert text.count(ids.alias("CASE-080").encode()) >= 3
            assert f"../{there.parent.name}/{Path(index.targets[str(there)]).name}".encode() in text
            assert b"#7=DESCRIPTIVE_REPRESENTATION_ITEM('material','AISI 304')" in text
            assert b"1.25,-2.0,0." in text and b"25 mm" in text and b"(#2)" in text
        dxf_output = first / "corpus" / index.targets[str(drawing)]
        assert "sources/gitlab/drawings" in dxf_output.as_posix()
        assert ids.alias("Rae O'Neil").encode() in dxf_output.read_bytes()
        assert len([p for p in (first / "corpus").rglob("*") if p.is_file()]) == 3
        report = (first / "README.txt").read_text()
        assert "Output files: 3" in report and "unsupported format" in report and "invalid," in report
        assert "unhandled.bin" not in report and "broken.stp" not in report
        unicode_header = document("a.step", "b.step").replace(b"Rae O''Neil", br"M\X2\00FC\X0\ller")
        assert "Müller" in step.discover(unicode_header)
    print("STEP identity, links, dimensions, deterministic rerun and omission checks passed.")


if __name__ == "__main__":
    check()
