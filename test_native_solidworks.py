"""Check metadata fields, directory validation and reproducible packaging without private CAD fixtures."""
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import struct
import tempfile
import unittest
import zipfile

import inspect_native
import native_solidworks
import pipeline
import stream_corpus

CORE = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <dc:creator>Example Designer</dc:creator><cp:lastModifiedBy>LAB\\example</cp:lastModifiedBy>
 <dc:lastModifiedBy>Second Example Author</dc:lastModifiedBy>
 <dc:subject>25 mm</dc:subject></cp:coreProperties>"""
APP = b"""<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
 <Company>Example Engineering Co</Company><Manager>administrator</Manager>
 <Application>Example CAD</Application></Properties>"""


def container(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, data in members:
            out.writestr(name, data)
    blob = bytearray(stream.getvalue())
    end = blob.rindex(b"PK\x05\x06")
    count = struct.unpack_from("<H", blob, end + 10)[0]
    central = struct.unpack_from("<I", blob, end + 16)[0]
    blob[end:end + 4] = b"END!"
    for _ in range(count):
        local = struct.unpack_from("<I", blob, central + 42)[0]
        length, extra, comment = struct.unpack_from("<3H", blob, central + 28)
        blob[local:local + 4] = b"FILE"
        blob[central:central + 4] = b"LIST"
        name = bytes(blob[local + 30:local + 30 + length]).translate(native_solidworks.NAME_BYTES)
        blob[local + 30:local + 30 + length] = name
        blob[central + 46:central + 46 + length] = name
        central += 46 + length + extra + comment
    return b"TEST\0\0\0\4" + bytes(blob)


class NativeProperties(unittest.TestCase):
    def test_typed_identity_only(self):
        sample = container([("docProps/app.xml", APP), ("docProps/core.xml", CORE),
                            ("Contents/Definition", b"Engineering geometry")])
        words = inspect_native.discover(sample)
        self.assertTrue({"Example Designer", "LAB\\example", "Example Engineering Co", "Second Example Author"} <= words)
        self.assertFalse({"25 mm", "Example CAD", "administrator"} & words)
        self.assertTrue(any(kind == "account" for kind, _, _ in words.forms))

    def test_invalid_property_and_ambiguous_directory(self):
        sample = container([("docProps/core.xml", CORE)])
        entry = native_solidworks.property_streams(sample)["docProps/core.xml"]
        damaged = bytearray(sample)
        damaged[entry[0] + entry[1] // 2] ^= 1
        with self.assertRaises(ValueError):
            inspect_native.discover(bytes(damaged))
        inconsistent = bytearray(sample)
        inconsistent[sample.index(b"FILE") + 14] ^= 1
        with self.assertRaises(ValueError):
            inspect_native.discover(bytes(inconsistent))
        with self.assertRaises(ValueError):
            inspect_native.discover(container([("docProps/core.xml", b"<!DOCTYPE a [<!ENTITY b 'x'>]><a/>")]))
        self.assertFalse(set(inspect_native.discover(b"other native input")))

    def test_streaming_discovery_carries_words_and_forms(self):
        data = container([("docProps/core.xml", CORE)])
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp).resolve()
            name = "sample.sldprt"
            (cache / name).write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            row = dict(path=name, ext=".sldprt", bytes=len(data), md5=hashlib.md5(data).hexdigest(),
                       source_sha=sha)
            status, reason, observed_sha, words, forms = stream_corpus.source_identities(
                row, SimpleNamespace(cache=cache, offline=True))
            self.assertEqual(status, "omitted")
            self.assertIn("metadata scanned", reason)
            self.assertEqual(observed_sha, sha)
            self.assertIn(b"second example author", words)
            self.assertTrue(any(kind == "account" for kind, raw, target in forms))

    def test_packaged_native_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pipeline._write_code(directory)
            expected = Path(native_solidworks.__file__).read_bytes()
            self.assertEqual((directory / "code/native_solidworks.py").read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
