"""Independently compare the geometry of matching private STEP inputs and outputs."""

import argparse
import math
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer

from pipeline import Identities, PathIndex


def geometry(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError("reader rejected file")
    roots = reader.TransferRoots()
    if roots == 0:
        return (0,), ()
    shape = reader.OneShape()
    counts = [roots]
    for kind in (TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX):
        explorer = TopExp_Explorer(shape, kind)
        count = 0
        while explorer.More():
            count += 1
            explorer.Next()
        counts.append(count)
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    area, volume = GProp_GProps(), GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, area)
    BRepGProp.VolumeProperties_s(shape, volume)
    bounds = []
    if not box.IsVoid():
        for point in (box.CornerMin(), box.CornerMax()):
            bounds.extend((point.X(), point.Y(), point.Z()))
    return tuple(counts), (*bounds, area.Mass(), volume.Mass())


def check(root, attempt, key_file):
    key = key_file.read_bytes()
    paths = [p.relative_to(root) for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    mappings = PathIndex(paths, Identities(key))
    compared = geometry_bearing = failures = 0
    for name in paths:
        if name.suffix.lower() not in {".step", ".stp"}:
            continue
        path = attempt / "corpus" / mappings.targets[str(name)]
        if not path.is_file():
            failures += 1
            continue
        try:
            a, x = geometry(root / name)
            b, y = geometry(path)
            if a != b or len(x) != len(y) or any(not math.isclose(u, v, rel_tol=1e-9, abs_tol=1e-8)
                                                 for u, v in zip(x, y)):
                failures += 1
            else:
                compared += 1
                geometry_bearing += bool(a[0])
        except (RuntimeError, ValueError):
            failures += 1
    print(f"Matching STEP reader results: {compared}. Files with transferable roots: {geometry_bearing}. "
          f"Missing or mismatched files: {failures}.")
    return 0 if compared and not failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(check(args.input, args.attempt, args.key_file))
