"""Check VRML97 redaction and compare private sample geometry with VTK 9.5.2."""

import argparse
import contextlib
import csv
import hashlib
import io
import json
import struct
import tempfile
from collections import Counter
from pathlib import Path

from pipeline import Identities, PathIndex, run
import vrml


TRIANGLE = b"""#VRML V2.0 utf8 Private Header
# comment with a private mailbox
WorldInfo { title "Caldera Assembly" info ["Author: Piper Example" "Company: Caldera Assembly"] }
NavigationInfo { type ["EXAMINE", "ANY"] }
DEF PiperExample Viewpoint { description "Front view" position 0 0 8 }
Transform { translation 1 2 3 children [
  DEF Caldera Shape {
    appearance Appearance { material Material { diffuseColor 0.1 0.2 0.3 } }
    geometry IndexedFaceSet {
      coord Coordinate { point [0 0 0, 1 0 0, 0 1 0] } coordIndex [0, 1, 2, -1]
    }
  }
  Transform { translation 3 2 1 children [USE Caldera] }
] }
"""
LINKED = b"""#VRML V2.0 utf8
WorldInfo { title "Piper Example" }
Anchor { url ["#PiperExample", "Caldera%20Assembly.wrl#PiperExample"] }
DEF PiperExample Viewpoint { description "top" }
Inline { url ["Caldera%20Assembly.wrl"] }
"""


def geometry(path):
    from vtkmodules.vtkCommonCore import vtkOutputWindow, vtkStringOutputWindow
    from vtkmodules.vtkIOImport import vtkVRMLImporter
    from vtkmodules.vtkRenderingCore import vtkRenderWindow
    from vtkmodules.util.numpy_support import vtk_to_numpy
    import vtkmodules.vtkRenderingOpenGL2  # Registers the offscreen window backend.

    error_output = vtkStringOutputWindow()
    prior = vtkOutputWindow.GetInstance()
    vtkOutputWindow.SetInstance(error_output)
    try:
        reader = vtkVRMLImporter()
        window = vtkRenderWindow()
        window.SetOffScreenRendering(1)
        reader.SetRenderWindow(window)
        reader.SetFileName(str(path))
        if not reader.Update() or "ERR|" in error_output.GetOutput() or "ERROR:" in error_output.GetOutput():
            raise ValueError("independent reader rejected scene")
        renderer = window.GetRenderers().GetFirstRenderer()
        if renderer is None:
            raise ValueError("independent reader produced no renderer")
        actors = renderer.GetActors()
        actors.InitTraversal()
        digest = hashlib.sha256()
        counts = Counter()
        def array(vtk_array):
            if vtk_array is None:
                digest.update(b"none")
                return
            a = vtk_to_numpy(vtk_array)
            digest.update(repr((str(a.dtype), a.shape)).encode() + b"\x00" + a.tobytes())
        for _ in range(actors.GetNumberOfItems()):
            actor = actors.GetNextActor()
            counts["actors"] += 1
            matrix = actor.GetMatrix()
            material = actor.GetProperty()
            values = [matrix.GetElement(i, j) for i in range(4) for j in range(4)]
            values.extend((*material.GetDiffuseColor(), *material.GetSpecularColor(), *material.GetAmbientColor(),
                           material.GetAmbient(), material.GetDiffuse(), material.GetSpecular(),
                           material.GetSpecularPower(), material.GetOpacity()))
            digest.update(struct.pack("!" + "d" * len(values), *values))
            mapper = actor.GetMapper()
            if mapper is None:
                counts["empty_actors"] += 1
                digest.update(b"empty-actor")
                continue
            mapper.Update()
            mesh = mapper.GetInput()
            if mesh is None or not mesh.IsA("vtkPolyData"):
                raise ValueError("missing polygonal mesh")
            counts["points"] += mesh.GetNumberOfPoints()
            counts["cells"] += mesh.GetNumberOfCells()
            pts = mesh.GetPoints()
            array(pts.GetData() if pts else None)
            for cells in (mesh.GetVerts(), mesh.GetLines(), mesh.GetPolys(), mesh.GetStrips()):
                array(cells.GetOffsetsArray())
                array(cells.GetConnectivityArray())
            for attributes in (mesh.GetPointData(), mesh.GetCellData()):
                for i in range(attributes.GetNumberOfArrays()):
                    array(attributes.GetArray(i))
        return digest.digest(), counts
    finally:
        vtkOutputWindow.SetInstance(prior)


def roundtrip(raw, ids, paths, name):
    result = vrml.rewrite(raw, ids.alias, ids, lambda p: paths.reference(name, p))
    vrml.discover(result)
    return result


def synthetic(check_geometry):
    from urllib.parse import quote
    names = [Path("Caldera Assembly.wrl"), Path("Connected.wrl")]
    ids = Identities(b"synthetic-test-key")
    paths = PathIndex(names, ids)
    for raw in (TRIANGLE, LINKED):
        for value in vrml.discover(raw):
            ids.learn(value)
    one = roundtrip(TRIANGLE, ids, paths, str(names[0]))
    two = roundtrip(LINKED, ids, paths, str(names[1]))
    for s in (b"Caldera", b"Piper", b"Private Header", b"private mailbox"):
        assert s not in one + two
    assert ids.alias("Piper Example").encode() in one and ids.alias("Piper Example").encode() in two
    assert b"Front view" in one and b'"EXAMINE"' in one
    assert one.count(ids.alias("Caldera").encode()) == 2
    target = quote(paths.targets[str(names[0])]).encode()
    assert target + b"#" + ids.alias("PiperExample").encode() in two
    assert b'"' + target + b'"' in two
    assert paths.link_counts["resolved in input inventory"] == 2
    technical = b'#VRML V2.0 utf8\nWorldInfo {info ["Diameter: 12.5 mm", "Author: Front"]}'
    cleaned = roundtrip(technical, ids, paths, str(names[0]))
    assert b"Diameter: 12.5 mm" in cleaned and b"Author: Front" not in cleaned
    visible = b'#VRML V2.0 utf8\nShape {geometry Text {string ["10mm", "Piper Example"] fontStyle FontStyle {family "SERIF"}}}'
    assert b"10mm" in roundtrip(visible, ids, paths, str(names[0]))
    assert b"Piper" not in roundtrip(visible, ids, paths, str(names[0]))
    escaped = b'#VRML V2.0 utf8\nWorldInfo {title "A#B\\"C" } # last line'
    assert b"A#B" not in roundtrip(escaped, ids, paths, str(names[0]))
    for source in (
        "#VRML V1.0 ascii\nSeparator {}",
        "#VRML V2.0 utf8\nScript {url \"javascript:example()\"}",
        "#VRML V2.0 utf8\nPROTO Privacy [] { Group{} }",
        "#VRML V2.0 utf8\nGroup { children [USE Missing]}",
        "#VRML V2.0 utf8\nDEF Alpha Group {} DEF ALPHA Group {}",
        "#VRML V2.0 utf8\nWorldInfo {unknown \"secret\"}",
        "#VRML V2.0 utf8\nInline {url \"https://private.example/file.wrl\"}",
        "#VRML V2.0 utf8\nShape {geometry IndexedFaceSet {coordIndex [10]}}",
        "#VRML V2.0 utf8\nShape {geometry Box {size 1 1}}",
        "#VRML V2.0 utf8\nShape {geometry Box {size 1 1 1}} unexpected",
        "#VRML V2.0 utf8\nAnchor {url \"#Missing\"}",
    ):
        try:
            roundtrip(source.encode(), ids, paths, str(names[0]))
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported or malformed scene was emitted")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "input"
        source.mkdir()
        for p, data in zip(names, (TRIANGLE, LINKED)):
            (source / p).write_bytes(data)
        (source / "active.wrl").write_bytes(b'#VRML V2.0 utf8\nScript {}')
        (root / "key").write_bytes(b"x" * 32)
        attempt = root / "attempt-1"
        with contextlib.redirect_stdout(io.StringIO()):
            run(source, attempt, root / "key", 16 * 1024)
        assert len(list((attempt / "corpus").rglob("*.wrl"))) == 2
        assert (attempt / "code" / "vrml.py").is_file()
        assert (attempt / "code" / "check_vrml.py").is_file()
        assert "Output files: 2." in (attempt / "README.txt").read_text()
    if check_geometry:
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f"scene{i}.wrl" for i in range(3)]
            for p, data in zip(paths, (TRIANGLE, one, one.replace(b"1 0 0, 0 1 0", b"2 0 0, 0 1 0"))):
                p.write_bytes(data)
            orig, clean, changed = (geometry(p) for p in paths)
            assert orig == clean and orig != changed and orig[1]["cells"] >= 2


def sample(root, manifest):
    paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".wrl")
    ids = Identities(b"private-verification-only")
    index = PathIndex([p.relative_to(root) for p in paths], ids)
    counts, good, hashes = Counter(), [], set()
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "verified.wrl"
        for p in paths:
            data = p.read_bytes()
            hashes.add((len(data), hashlib.md5(data).hexdigest()))
            counts["input_files"] += 1
            try:
                words = vrml.discover(data)
            except (ValueError, OSError):
                counts["outside_supported_grammar"] += 1
                continue
            for word in words:
                ids.learn(word)
            good.append(p)
        for p in good:
            raw = p.read_bytes()
            result = roundtrip(raw, ids, index, p.relative_to(root).as_posix())
            source_words = vrml.discover(raw)
            if source_words.intersection(vrml.discover(result)):
                raise ValueError("source metadata or node identity survived in a typed field")
            counts["changed_identity_fields_grouped_by_value"] += len(source_words)
            dst.write_bytes(result)
            before, stats = geometry(p)
            after, stats2 = geometry(dst)
            if before != after or stats != stats2:
                raise ValueError("source and transformed geometry differ")
            counts["vtk_pairs_equal"] += 1
            counts["files_with_mesh_cells"] += int(stats["cells"] > 0)
            counts.update(stats)
        counts["unique_input_size_md5"] = len(hashes)
        if manifest:
            with manifest.open(encoding="utf-8", newline="") as stream:
                rows = csv.DictReader(stream, delimiter="\t")
                original = {(int(r["bytes"]), r["md5 (blank for files uploaded in parts)"].lower())
                            for r in rows if r["# path"].lower().endswith(".wrl")}
            counts["unique_inputs_matched_manifest"] = len(hashes & original)
            if not hashes <= original:
                raise ValueError("private sample differs from organizer manifest")
    return counts


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--vtk", action="store_true", help="check synthetic scene with VTK")
    args.add_argument("--sample-dir", type=Path, help="verify private .wrl files with VTK; print totals only")
    args.add_argument("--manifest", type=Path, help="check sample sizes and MD5s against this organizer TSV")
    options = args.parse_args()
    synthetic(options.vtk or options.sample_dir is not None)
    if options.sample_dir:
        print(json.dumps(sample(options.sample_dir, options.manifest), sort_keys=True))
    else:
        print("Synthetic VRML97 checks passed.")
