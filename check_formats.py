"""Exercise binary/text meshes, irreversible PDF redaction and intact engineering content."""

import struct
from pathlib import Path
from unittest.mock import patch

import drawing_pdf
import mesh
from pipeline import Identities, PathIndex, structural


def pdf_fixture():
    pdf = drawing_pdf.library()
    doc = pdf.open()
    p = doc.new_page(width=360, height=240)
    p.draw_line((12, 15), (300, 15))
    icon = pdf.Pixmap(pdf.csRGB, pdf.IRect(0, 0, 12, 12), 0)
    icon.clear_with(106)
    p.insert_image(pdf.Rect(20, 130, 40, 150), pixmap=icon)
    p.insert_text((14, 60), "Author: Morgan Quill")
    p.insert_text((14, 85), "Bore 12.50 mm   Morgan Quill   tolerance 0.02 mm")
    p.insert_text((240, 220), "Morgan Quill", rotate=90)
    private_copy = doc.get_new_xref()
    doc.update_object(private_copy, "<<>>")
    doc.update_stream(
        private_copy, b"%!PS-Adobe-3.0\n%%Title: Vega Assembly.pdf\nMorgan Quill"
    )
    doc.xref_set_key(
        p.xref, "PieceInfo", f"<</Illustrator<</Private {private_copy} 0 R>>>>"
    )
    doc.xref_set_key(p.xref, "Author", pdf.get_pdf_str("Morgan Quill"))
    doc.set_metadata(
        {"author": "Morgan Quill", "title": "Vega Assembly", "producer": "CAD export"}
    )
    number_tree = doc.get_new_xref()
    doc.update_object(
        number_tree,
        "<</Limits[0 0]/Nums[0<</S/r/St 3/P<FEFF005600650067006100200041007300730065006D0062006C0079>>>]>>",
    )
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"<</Kids[{number_tree} 0 R]>>")
    doc.set_xml_metadata(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:RDF><rdf:Description>'
        "<dc:creator><rdf:Seq><rdf:li>Morgan Quill</rdf:li></rdf:Seq></dc:creator>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    doc.set_toc([[1, "Morgan Quill", 1]])
    annot = p.add_text_annot((285, 45), "Morgan Quill")
    annot.set_info(title="Morgan Quill", subject="Authored by Morgan Quill")
    p.insert_link(
        {
            "kind": pdf.LINK_URI,
            "from": pdf.Rect(12, 110, 110, 122),
            "uri": "https://private.example.com/reference",
        }
    )
    ascii_stl = (
        b"solid Morgan Quill\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\n"
        b"vertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid Morgan Quill\n"
    )
    doc.embfile_add("Vega Assembly.stl", ascii_stl)
    attachment = p.add_file_annot((260, 85), ascii_stl, "Vega Assembly.stl")
    attachment.set_info(title="Morgan Quill")
    data = doc.tobytes()
    doc.close()
    return data, ascii_stl


def main():
    pdf = drawing_pdf.library()
    fixture, ascii_stl = pdf_fixture()
    ids = Identities(bytes(range(32)))
    for value in drawing_pdf.discover(fixture):
        ids.learn(value)
    paths = PathIndex(list(map(Path, ["drawing.pdf", "Vega Assembly.stl"])), ids)

    def reference(value):
        return paths.reference("drawing.pdf", value)

    transformed = drawing_pdf.rewrite(fixture, ids.alias, ids, reference)
    with patch.object(pdf, "get_pdf_now", return_value="D:19990101000000Z"):
        assert transformed == drawing_pdf.rewrite(fixture, ids.alias, ids, reference)
    with pdf.open(stream=transformed) as doc:
        p = doc[0]
        author = ids.alias("Morgan Quill")
        text = p.get_text()
        assert "Morgan" not in text and text.count(author) >= 3, text
        assert "Bore 12.50 mm" in text and "tolerance 0.02 mm" in text, text
        assert doc.metadata["author"] == author and author in doc.get_xml_metadata()
        assert doc.xref_get_key(p.xref, "PieceInfo")[0] == "null"
        assert doc.xref_get_key(p.xref, "Author") == ("string", author)
        rules = doc.get_page_labels()
        assert rules == [
            {
                "startpage": 0,
                "prefix": ids.alias("Vega Assembly"),
                "firstpagenum": 3,
                "style": "r",
            }
        ]
        assert doc.get_toc()[0][1] == author and any(
            a.info["title"] == author for a in p.annots()
        )
        assert p.get_links()[0]["uri"].startswith("https://site-")
        with pdf.open(stream=fixture) as original:
            assert [v["items"] for v in p.get_drawings()] == [
                v["items"] for v in original[0].get_drawings()
            ]
            assert (
                p.get_image_info(hashes=True)[0]["digest"]
                == original[0].get_image_info(hashes=True)[0]["digest"]
            )
        (filename,) = doc.embfile_names()
        attached = next(a for a in p.annots() if a.type[1] == "FileAttachment")
        assert attached.file_info[
            "filename"
        ] == filename and attached.get_file() == doc.embfile_get(filename)
        assert filename == paths.targets[
            "Vega Assembly.stl"
        ] and author.encode() in doc.embfile_get(filename)
        for i in range(1, doc.xref_length()):
            contents = doc.xref_object(i).encode() + (doc.xref_stream(i) or b"")
            assert (
                b"Morgan Quill" not in contents
                and "Morgan Quill".encode("utf-16-be") not in contents
            )
    with pdf.open() as document:
        page = document.new_page()
        document.set_metadata({"title": "480237"})
        page.insert_text((10, 50), "Drawing 480237")
        page.insert_text((10, 90), "Length 480237 mm")
        data = drawing_pdf.rewrite(document.tobytes(), ids.alias, ids, reference)
        with pdf.open(stream=data) as saved:
            text = saved[0].get_text()
            assert ids.alias("480237") in text and text.count("480237") == 1
            assert "480237 mm" in text
    binary = (
        b"Morgan Quill COLOR=\x10\x20\x30\xff".ljust(80, b"\0")
        + struct.pack("<I", 1)
        + struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 31775)
    )
    updated = mesh.rewrite(binary, ids.alias, ids, reference)
    assert (
        updated[80:] == binary[80:]
        and b"COLOR=\x10\x20\x30\xff" in updated[:80]
        and b"Morgan" not in updated
    )
    text_mesh = mesh.rewrite(ascii_stl, ids.alias, ids, reference)
    assert text_mesh.splitlines()[1:-1] == ascii_stl.splitlines()[1:-1]
    assert structural("M3x10_socket-head_screw") and not structural("Vega Assembly")
    print(
        "PDF text, metadata, links, attachments, object streams, vector geometry and both STL encodings passed."
    )


if __name__ == "__main__":
    main()
