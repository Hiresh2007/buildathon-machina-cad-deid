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


# A stock designation needs a recognized standard, its matching product family,
# and a size. A manufacturer's name or an extra project number disqualifies it.
STOCK_NUMBER = r"\d{1,4}(?:[.,]\d{1,3})?"
STOCK_SIZES = r"[ldh]?" + STOCK_NUMBER + r"(?:x" + STOCK_NUMBER + r"){0,2}"
THREAD_SIZE = r"m" + STOCK_NUMBER + "x" + STOCK_NUMBER + r"(?:x" + STOCK_NUMBER + r")?"
STRENGTH_CLASS = r"(?: (?:4[.,][68]|5[.,][68]|6[.,]8|8[.,]8|9[.,]8|10[.,]9|12[.,]9)(?: a2k)?)?"
STEEL_GRADE = r"(?: s(?:235|275|355)(?:jr|j0|j2|k2)?h?)?"
STOCK_STANDARDS = {
    "iso4762": (r"(?:socket head cap screw|hexagon socket head cap screw|screw)", THREAD_SIZE, STRENGTH_CLASS),
    "iso4014": (r"(?:hexagon head bolt|hex head bolt|bolt)", THREAD_SIZE, STRENGTH_CLASS),
    "iso4017": (r"(?:hexagon head screw|hex head screw|screw)", THREAD_SIZE, STRENGTH_CLASS),
    "en10219": (r"(?:(?:rectangular|square|circular) hollow section)", STOCK_SIZES, STEEL_GRADE),
    "en10056": (r"(?:angle bar|angle equal|angle unequal)", STOCK_SIZES, STEEL_GRADE),
    "en10058": (r"flat bar", STOCK_SIZES, STEEL_GRADE),
    "en10059": (r"square bar", STOCK_SIZES, STEEL_GRADE),
    "en10060": (r"round bar", STOCK_SIZES, STEEL_GRADE),
}
STOCK_STANDARD = re.compile(r"(?<![a-z0-9])(?P<series>iso|en) ?(?P<number>[0-9]{4,5})(?![0-9])")
STOCK_LABEL = {
    code: re.compile(r"(?:" + kind + " " + size + r" std|std " + kind + " " + size
                     + "|" + kind + " std " + size + ")" + material)
    for code, (kind, size, material) in STOCK_STANDARDS.items()
}


def stock_designation(value):
    """Recognize a fully specified catalog part without exempting mixed or project-specific titles."""
    if not 9 <= len(value) <= 110:
        return False
    text = value.casefold()
    if "iso" not in text and "en" not in text:
        return False
    text = " ".join(text.replace("_", " ").replace("×", "x").split())
    spec = STOCK_STANDARD.search(text)
    if not spec:
        return False
    label = STOCK_LABEL.get(spec["series"] + spec["number"])
    return bool(label and label.fullmatch(text[:spec.start()] + "std" + text[spec.end():]))


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


# The corpus packs a GitHub owner and repository into one directory. Restrict
# parsing to the provider prefix and unambiguous pairs of valid components.
GITHUB_OWNER = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")
GITHUB_REPO = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")


def repository_parts(value, parents):
    if len(parents) < 2 or tuple(part.casefold() for part in parents[-2:]) != ("sources", "github"):
        return None
    pieces = value.split("__")
    if len(pieces) != 2:
        return None
    owner, repo = pieces
    if (len(owner) > 39 or not GITHUB_OWNER.fullmatch(owner) or
            not GITHUB_REPO.fullmatch(repo) or repo in {".", ".."}):
        return None
    return owner, repo


def repository_alias(parts, alias):
    # Individual aliases may have a URL form. Encode separators so the joined
    # pair remains a single directory in physical paths and PDF titles alike.
    def component(value):
        name = alias(value).removeprefix("https://").removeprefix("http://")
        return name.replace("?ref=", "-ref-").replace("/", "%2F").replace(chr(92), "%5C")
    return "__".join(component(value) for value in parts)


def engineering_directory(value, parents, repository_folders=True):
    name = re.sub(r"[ _-]+", " ", canonical(value))
    if name not in ENGINEERING_DIRECTORIES:
        return False
    # Preserve the role of the component in provider and home-directory paths.
    if parents and canonical(parents[-1]) in {"users", "home"}:
        return False
    if len(parents) in {2, 3} and canonical(parents[0]) == "sources":
        provider = canonical(parents[1])
        if (repository_folders and len(parents) == 3 and provider == "github"
                and repository_parts(parents[2], parents[:2])):
            return True
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
