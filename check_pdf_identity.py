"""Check account identity evidence, title/model spellings and checkpointed discovery."""

import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
from urllib.parse import quote

import drawing_pdf
import pdf_identity
from pipeline import Identities, PathIndex, run
import stream_corpus


def document(author, title, visible):
    pdf = drawing_pdf.library()
    with pdf.open() as doc:
        page = doc.new_page()
        doc.set_metadata({"author": author, "title": title})
        for i, line in enumerate(visible):
            page.insert_text((35, 50 + i * 20), line, fontsize=9)
        page.draw_line((25, 155), (145, 155), color=(1, 0, 0))
        return doc.tobytes(no_new_id=True)


def check_drawing_cells():
    pdf = drawing_pdf.library()
    ids = Identities(b"fixture-only identity key")
    rows = [
        ("Gezeichnet", "Marin Keller"),
        ("Dibujado por", "Eva Rios"),
        ("Chkd", "ZK"),
        ("DWG NO.", "QV-2740"),
        ("Drawing number", "9001"),
        ("Part number", "M12"),
        ("Part number", "ISO-2768"),
    ]
    with pdf.open() as doc:
        page = doc.new_page()
        for i, (label, value) in enumerate(rows):
            y = 60 + 35 * i
            page.insert_text((40, y), label, fontsize=9)
            page.insert_text((51 + pdf.get_text_length(label, fontsize=9), y), value, fontsize=9)
        doc.set_metadata({"title": "fixture"})
        page.draw_line((25, 350), (225, 350))
        data = doc.tobytes()
    found = drawing_pdf.discover(data)
    assert {value for _, value in rows[:4]} <= found
    assert not {value for _, value in rows[4:]} & found
    for value in found:
        ids.learn(value)
    output = drawing_pdf.rewrite(data, ids.alias, ids, lambda p: p)
    with pdf.open(stream=output) as doc:
        text = doc[0].get_text()
        assert all(ids.alias(value) in text and value not in text for _, value in rows[:4])
        assert all(value in text for _, value in rows[4:])
        assert doc[0].get_drawings()


def check_link_rectangles():
    pdf = drawing_pdf.library()
    ids = Identities(b"test key for linked drawings")
    ids.learn("Fixture Owner")
    ids.register_hosts({"orbitallab.test"})
    paths = PathIndex([Path("drawings/sample.pdf"), Path("models/fixture-part.step")], ids)
    expected_model = paths.reference("drawings/sample.pdf", "../models/fixture-part.step")
    url = "https://orbitallab.test/support"
    with pdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 60), "Fixture Owner")
        page.insert_text((40, 95), "Fixture Owner")
        for rect in ((35, 45, 150, 65), (35, 115, 150, 135)):
            page.insert_link({"kind": pdf.LINK_LAUNCH, "from": pdf.Rect(rect), "file": "../models/fixture-part.step"})
        page.insert_link({"kind": pdf.LINK_URI, "from": pdf.Rect(35, 79, 150, 99), "uri": url})
        original = doc.tobytes()
    result = drawing_pdf.rewrite(original, ids.alias, ids, lambda value: paths.reference("drawings/sample.pdf", value))
    with pdf.open(stream=result) as doc:
        links = {tuple(data["from"]): data for data in doc[0].get_links()}
        assert len(links) == 3
        assert links[(35, 45, 150, 65)]["file"] == links[(35, 115, 150, 135)]["file"] == expected_model
        assert links[(35, 79, 150, 99)]["uri"] == ids.alias(url)
        assert "Fixture Owner" not in doc[0].get_text()


def check_contact_domains():
    sources = [
        document("", "", ["Support: help@orbitallab.test", "Sales: sales@orbitallab.test"]),
        document("", "", ["https://ORBITALLAB.test/SpecA", "See (https://orbitallab.test/speca)."]),
        document("", "", ["orbitallab.test", "www.orbitallab.test", "bracket.sldprt"]),
        document("", "", ["help@secondlab.test", "west.orbitallab.test"]),
    ]
    ids = Identities(b"fixture for contact domains")
    found = [drawing_pdf.discover(data) for data in sources]
    for observations in found:
        for value in observations:
            ids.learn(value)
    pdf_identity.resolve({entry for observations in found for entry in observations.forms}, ids)
    host = ids.alias("orbitallab.test")
    second = ids.alias("secondlab.test")
    assert host != second and host.endswith(".example.invalid")
    help_address = ids.alias("help@orbitallab.test")
    sales_address = ids.alias("sales@orbitallab.test")
    assert help_address != sales_address and all(value.endswith("@" + host) for value in [help_address, sales_address])
    specific = ids.alias("https://orbitallab.test/SpecA")
    assert specific == ids.alias("https://ORBITALLAB.test/SpecA")
    assert specific != ids.alias("https://orbitallab.test/speca")
    assert ids.redact_text("See (https://orbitallab.test/speca).") == "See (" + ids.alias("https://orbitallab.test/speca") + ")."
    assert ids.redact_text("bracket.sldprt") == "bracket.sldprt"
    sub = ids.host_alias("west.orbitallab.test")
    assert sub.endswith("." + host) and sub != host
    expected = ((help_address, sales_address), (specific, ids.alias("https://orbitallab.test/speca")),
                (host, "https://www." + host, "bracket.sldprt"), (ids.alias("help@secondlab.test"), sub))
    for data, values in zip(sources, expected):
        out = drawing_pdf.rewrite(data, ids.alias, ids, lambda p: p)
        with drawing_pdf.library().open(stream=out) as pdf:
            text = "".join(pdf[0].get_text().split())
            assert all(value in text for value in values)
            assert "orbitallab.test" not in text.casefold()


def main():
    check_link_rectangles()
    check_drawing_cells()
    check_contact_domains()
    source = {
        "contact.pdf": document("", "", ["help@orbitallab.test"]),
        "domain.pdf": document("", "", ["orbitallab.test"]),
        "a.pdf": document(
            r"NORTH\mquill (DESK-1)", r"C:\models\North Pin.slddrw", ["Drawn by: mquill", "Length 25 mm"]
        ),
        "b.pdf": document(r"NORTH\mquill (DESK-2)", "North Pin.sldprt", [r"NORTH\mquill (DESK-2)"]),
        "c.pdf": document("mquill", "plain", ["Drawn by: mquill"]),
        "d.pdf": document(r"NORTH\operator9 (DESK-7)", "plain", ["Author: operator9"]),
        "e.pdf": document(r"SOUTH\operator9 (DESK-7)", "plain", ["Author: operator9"]),
        "f.pdf": document("operator9", "plain", ["Author: operator9"]),
        "g.pdf": document("unrelated", "plain", ["North Pin.sldprt", "Tolerance 0.02 mm"]),
        "h.pdf": document("unrelated", "plain", ['Model file: "Caldera Panel.sldprt"', "ZX-42.idw"]),
        "i.pdf": document("unrelated", "bracket.sldprt", ["Caldera Panel.sldprt", "bracket.sldprt"]),
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = root / "input"
        cache.mkdir()
        manifest = root / "manifest.tsv"
        with manifest.open("w") as listing:
            for name, data in source.items():
                (cache / name).write_bytes(data)
                listing.write(f"{name}\t{len(data)}\t{hashlib.md5(data).hexdigest()}\tcorpus/{quote(name)}\n")
        state = root / "state"
        state.mkdir()
        key = bytes(range(32))
        (state / "alias.key").write_bytes(key)
        direct = root / "local/attempt-1"
        archived = root / "external/attempt-1"
        argv = [
            "--manifest",
            str(manifest),
            "--cache",
            str(cache),
            "--offline",
            "--workers",
            "2",
            "--state",
            str(state),
            "--attempt",
            str(root / "staging/attempt-1"),
            "--archive",
            str(archived),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            run(cache, direct, state / "alias.key", 4 * 1024 * 1024, workers=2)
            try:
                stream_corpus.run(stream_corpus.options(argv + ["--work-limit", "3"]))
            except stream_corpus.Paused:
                pass
            stream_corpus.run(stream_corpus.options(argv))
        ids = Identities(key)
        forms = set()
        for data in source.values():
            found = drawing_pdf.discover(data)
            for word in found:
                ids.learn(word)
            forms.update(found.forms)
        pdf_identity.resolve(forms, ids)
        names = PathIndex(list(map(Path, source)), ids)
        outputs = {}
        for original in source:
            target = names.targets[original]
            result = (direct / "corpus" / target).read_bytes()
            assert result == (archived / "corpus" / target).read_bytes()
            with drawing_pdf.library().open(stream=result) as doc:
                outputs[original] = (doc.metadata, doc[0].get_text())
                assert doc[0].get_drawings()
        n = ids.alias(r"NORTH\mquill")
        assert all(outputs[k][0]["author"] == n and n in outputs[k][1] for k in ("a.pdf", "b.pdf", "c.pdf"))
        assert len({outputs[k][0]["author"] for k in ("d.pdf", "e.pdf", "f.pdf")}) == 3
        stem = ids.alias("North Pin")
        assert outputs["a.pdf"][0]["title"].endswith("/models/" + stem + ".slddrw")
        assert outputs["b.pdf"][0]["title"] == stem + ".sldprt"
        assert stem + ".sldprt" in outputs["g.pdf"][1]
        panel = ids.alias("Caldera Panel") + ".sldprt"
        assert panel in outputs["h.pdf"][1] and panel in outputs["i.pdf"][1]
        assert ids.alias("ZX-42") + ".idw" in outputs["h.pdf"][1]
        assert outputs["i.pdf"][0]["title"] == "bracket.sldprt"
        assert "bracket.sldprt" in outputs["i.pdf"][1]
        restored = Identities(key)
        pdf_identity.restore_directory_forms(cache, direct, list(map(Path, source)), restored)
        assert restored.replacements == ids.replacements
        assert ids.alias("orbitallab.test") in outputs["contact.pdf"][1]
        assert ids.alias("orbitallab.test") in outputs["domain.pdf"][1]
        report = direct / "README.txt"
        report.write_text(report.read_text().replace(pdf_identity.CONTACT_REPORT, ""))
        legacy = Identities(key)
        pdf_identity.restore_directory_forms(cache, direct, list(map(Path, source)), legacy)
        assert not legacy.hosts and legacy.alias("orbitallab.test") != ids.alias("orbitallab.test")
        assert "Length 25 mm" in outputs["a.pdf"][1] and "Tolerance 0.02 mm" in outputs["g.pdf"][1]
        assert (direct / "code/pdf_identity.py").is_file()
    print("Typed account, conflicting domain, linked model title, physical text and resume checks passed.")


if __name__ == "__main__":
    main()
