"""Regression checks for SVG drawing geometry and ACIS counted metadata strings."""
import io
import zipfile
from xml.etree import ElementTree as ET

import fcstd
import sat
import svg
from pipeline import Identities


def check():
    ids = Identities(b"synthetic test key")
    ids.learn("Orbital Example")
    fixture = b'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:dc="http://purl.org/dc/elements/1.1/" viewBox="0 0 100 80">
    <metadata><dc:creator>Orbital Example</dc:creator></metadata>
    <path id="edge" d="M0 20L80 60"/><use href="#edge"/><image href="sister.svg"/>
    <text>Drawn by: Orbital Example</text><text>Length 18.5 mm</text></svg>'''
    assert "Orbital Example" in set(svg.discover(fixture))
    rendered = svg.rewrite(fixture, ids.alias, ids.redact_text, lambda v: "part.svg" if v == "sister.svg" else v)
    root = ET.fromstring(rendered)
    assert b"Orbital Example" not in rendered and ids.alias("Orbital Example").encode() in rendered
    assert root.find("{*}path").attrib == {"id": "edge", "d": "M0 20L80 60"}
    assert root.find("{*}use").get("href") == "#edge" and root.find("{*}image").get("href") == "part.svg"
    assert b"Length 18.5 mm" in rendered
    fixture = (b"700 0 1 0\n15 Orbital Example 8 ACIS 7.0 0 \n1 1e-6 1e-10\n"
               b"body $-1 -1 $-1 $-1 $-1 $-1 #\n"
               b"author-attrib $-1 @15 Orbital Example #\n"
               b"point $-1 -1 $-1 10 20 30 #\nEnd-of-ACIS-data\n")
    assert "Orbital Example" in set(sat.discover(fixture))
    restored = sat.rewrite(fixture, ids.alias, ids.redact_text, lambda x: x)
    assert b"Orbital Example" not in restored
    value = ids.alias("Orbital Example").encode()
    assert str(len(value)).encode() + b" " + value in restored
    assert b"point $-1 -1 $-1 10 20 30 #\n" in restored
    assert tuple(sat.discover(restored))
    doc = b'<Document><Properties><Property name="CreatedBy" type="App::PropertyString"><String value="Orbital Example"/></Property></Properties><Object name="Body001"/></Document>'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Document.xml", doc)
        z.writestr("PartShape.brp", b"synthetic immutable shape stream")
    assert "Orbital Example" in set(fcstd.discover(archive.getvalue()))
    revised = fcstd.rewrite(archive.getvalue(), ids.alias, ids.redact_text, lambda path: path)
    with zipfile.ZipFile(io.BytesIO(revised)) as z:
        assert z.testzip() is None
        assert b"Orbital Example" not in z.read("Document.xml")
        assert z.read("PartShape.brp") == b"synthetic immutable shape stream"
        assert ET.fromstring(z.read("Document.xml")).find("Object").get("name") == "Body001"
    print("SVG drawing geometry and links, SAT counted metadata, and native FreeCAD streams passed.")



if __name__ == "__main__":
    check()
