"""Recognize common engineering path components and measurements."""

import re
import unicodedata


GENERAL_NAMES = {
    "cad",
    "step",
    "stp",
    "dxf",
    "drawings",
    "drawing",
    "models",
    "model",
    "components",
    "component",
    "assemblies",
    "assembly",
    "parts",
    "part",
    "export",
    "exports",
    "files",
    "archive",
    "archives",
    "input",
    "output",
    "data",
    "neutral",
    "documents",
    "library",
    "mechanical",
    "hardware",
    "bracket",
    "bolt",
    "nut",
    "plate",
    "shaft",
    "washer",
    "source",
    "sources",
    "github",
    "gitlab",
    "grabcad",
    "dataset",
    "datasets",
    "public",
    "solidworks",
    "freecad",
    "autodesk",
    "inventor",
    "fusion360",
    "pdf",
    "images",
    "dwg",
    "iges",
    "igs",
    "stl",
    "vrml",
    "wrl",
    "sldprt",
    "sldasm",
    "slddrw",
    "ipt",
    "iam",
    "idw",
    "existing",
    "web",
    "catalog",
    "catalogs",
    "sat",
    "acis",
    "fcstd",
    "svg",
    "doc",
    "docs",
    "cadfiles",
    "resources",
    "assets",
    "examples",
    "src",
    "master",
    "main",
    "release",
    "releases",
    "3d",
    "2d",
    "modeling",
    "render",
    "renders",
    "print",
    "prints",
    "printing",
    "printable",
    "electrical",
    "electronics",
    "fixture",
    "fixtures",
    "schematic",
    "schematics",
    "layouts",
    "base",
    "body",
    "frame",
    "cover",
    "housing",
    "enclosure",
    "mount",
    "flange",
    "gasket",
    "bearing",
    "wheel",
    "pin",
    "screw",
    "hex",
    "socket",
    "head",
    "cap",
    "fastener",
    "spacer",
    "spring",
    "gear",
    "motor",
    "fan",
    "pump",
    "valve",
    "hinge",
    "pulley",
    "pipe",
    "tube",
    "top",
    "bottom",
    "left",
    "right",
    "upper",
    "lower",
    "front",
    "rear",
    "back",
    "side",
    "assembly",
    "assy",
    "sheet",
    "drawing",
    "section",
    "cross",
    "view",
    "detail",
    "dimensions",
    "mini",
    "micro",
    "eccentric",
    "linear",
    "round",
    "flat",
    "square",
    "small",
    "large",
    "long",
    "short",
    "ball",
    "roller",
    "end",
    "middle",
    "clamp",
    "spindle",
    "axle",
    "lock",
    "ring",
    "rod",
    "rail",
    "mounting",
    "block",
    "insert",
    "thread",
    "threaded",
    "elbow",
    "connector",
    "adapter",
    "coupler",
}
MEASURE = re.compile(
    r"(?:m\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)?|\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)*(?:mm|cm|inch|in))",
    re.I,
)


def structural(value):
    value = value.strip()
    tokens = re.split(r"[\s_\-]+", canonical(value))
    return bool(tokens) and all(
        word in GENERAL_NAMES or MEASURE.fullmatch(word) for word in tokens
    )



# These describe collections of artifacts or manufacturing operations. A repository,
# organization or user account can still have the same spelling.
ENGINEERING_DIRECTORIES = frozenset("""
    blueprints cadfiles cadmodels clamps datasheets diagrams documentation downloads drawingsheets
    dxffiles enclosures fabrication fasteners firmware fixtures gearboxes gerbers grippers
    harnesses holders layouts manuals machining machiningfiles manufacturing materials
    meshes motors originals outputs pcbs pdfs plans plates printables prototypes references
    renderings revisions schematics sheetmetal spacers stepfiles stls subassemblies
    templates tooling tutorials weldments washers wiring
""".split()) | {
    "assembly files", "assembly instructions", "assembly models", "bom exports",
    "cad documentation", "cad exports", "cad files", "cad models", "cut sheets",
    "drawing files", "electrical diagrams", "engineering drawings", "fabrication drawings",
    "laser cut", "laser cutting", "part drawings", "part files", "pdf drawings",
    "print files", "production drawings", "reference drawings", "sheet metal",
    "solidworks files", "step files", "sub assemblies", "technical drawings", "wiring diagrams",
}


def engineering_directory(value, parents):
    name = re.sub(r"[ _-]+", " ", canonical(value))
    if name not in ENGINEERING_DIRECTORIES:
        return False
    # Preserve the role of the component in provider and home-directory paths.
    if parents and canonical(parents[-1]) in {"users", "home"}:
        return False
    if len(parents) in {2, 3} and canonical(parents[0]) == "sources":
        provider = canonical(parents[1])
        if provider in {"github", "gitlab", "grabcad"} or (provider == "web" and len(parents) == 2):
            return False
    return True


def path_identities(path):
    yield path.stem
    for i, part in enumerate(path.parts[:-1]):
        if not engineering_directory(part, path.parts[:i]):
            yield part

def canonical(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


DIMENSION = re.compile(
    r"(?<![\w.])(?:m\d+(?:[.,]\d+)?(?:x\d+(?:[.,]\d+)?)?"
    r"|\d+(?:[.,]\d+)?(?:[x×]\d+(?:[.,]\d+)?)*\s*(?:mm|cm|um|µm|kg|inches|inch|rpm|mpa|psi))(?!\w)",
    re.I,
)
