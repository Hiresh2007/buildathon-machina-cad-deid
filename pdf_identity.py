"""Resolve only identity relationships attested by typed PDF person and title fields."""

from collections import defaultdict
from pathlib import PurePosixPath
import re

from naming import DIMENSION, canonical, structural


PART_REPORT = "Corroborated part codes share a pseudonym across drawings and models while retaining mechanical descriptors."

MODEL_EXTENSIONS = {".sldprt", ".sldasm", ".step", ".stp", ".ipt", ".iam", ".fcstd", ".sat", ".stl", ".3mf"}
PART_WORDS = frozenset("""
    assembly adapter axle bar bearing block body bolt bottom bracket bushing cable cap clamp clip collar
    connector cover coupling door enclosure flange frame front gasket gear guide handle head hinge holder
    housing insert knob left lever lid lower mount mounting motor nut panel pin pipe plate pulley rail rear
    right ring rod roller screw shaft shell side spacer spindle spring support switch top tube upper valve
    washer wheel
""".split())
PART_CODE = re.compile(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9][A-Za-z0-9._-]*(?: [A-Za-z0-9][A-Za-z0-9._-]*){0,2}\Z")
STANDARD_CODE = re.compile(r'^(?:DIN|ISO|ANSI|ASTM|ASME|IEC|SAE|EN|BS|JIS|REV|VER|V)[\s._-]*(?=\d)', re.I)


PERSON_FIELDS = {
    "author",
    "creator",
    "contributor",
    "owner",
    "designer",
    "drawnby",
    "checkedby",
    "approvedby",
    "createdby",
    "modifiedby",
    "lastsavedby",
}
ACCOUNT = re.compile(r"([\w.-]{1,64})\\([\w.-]{1,64})(?:\s+\(([\w. -]{1,64})\))?\Z")
ASSET = re.compile(
    r"\.(?:pdf|dwg|dxf|step|stp|stl|slddrw|sldprt|sldasm|ipt|iam|idw|fcstd|3mf|sat|wrl)\Z", re.I
)
GENERIC = {"admin", "administrator", "root", "user", "default", "guest", "owner", "operator", "public", "cad"}
SYSTEM_FOLDERS = {"users", "home", "desktop", "documents", "documents and settings", "program files"}
FILE_LABEL = re.compile(
    r"^\s*(?:file\s?name|source file|cad file|model file|drawing file|model|drawing|part file)\s*[:=]\s*",
    re.I,
)
PATH_PREFIX = re.compile(r"^(?:[a-z]:[\\/]|\\\\|\.\.?[\\/]|/(?:home|Users)/)", re.I)


def account(value):
    m = ACCOUNT.fullmatch(value.strip())
    if m is None:
        return None
    domain, login, computer = m.groups()
    if domain in {".", ".."} or login in {".", ".."} or ASSET.search(login):
        return None
    return domain + "\\" + login, login, computer


def asset(value):
    value = value.strip()
    if (
        not ASSET.search(value)
        or len(value) > 1024
        or any(ord(ch) < 32 for ch in value)
        or re.match(r"^[a-z][\w+.-]*://|^www\.", value, re.I)
    ):
        return None
    parts = value.replace("\\", "/").split("/")
    name = PurePosixPath(parts[-1])
    if not name.stem or name.stem in {".", ".."} or not name.suffix:
        return None
    return parts[:-1], name.stem, name.suffix


def asset_title(value, alias):
    folders, stem, extension = asset(value)

    def component(part):
        if (
            not part
            or part in {".", ".."}
            or canonical(part) in SYSTEM_FOLDERS
            or re.fullmatch("[a-zA-Z]:", part)
            or structural(part)
        ):
            return part
        return alias(part)

    return "/".join([*(component(part) for part in folders), component(stem) + extension])


def visible_asset(text):
    """Recognize a full CAD filename, an explicit file cell or a native CAD path."""
    marker = FILE_LABEL.match(text)
    left = marker.end() if marker else len(text) - len(text.lstrip())
    right = len(text.rstrip())
    if right > left + 2 and text[left] in {"'", '"'} and text[left] == text[right - 1]:
        left += 1
        right -= 1
        quoted = True
    else:
        quoted = False
    name = text[left:right]
    if asset(name) and (marker or quoted or PATH_PREFIX.match(name) or re.fullmatch(r"[\w.+-]+", name)):
        return left, right
    return None


def restore_directory_forms(root, attempt, paths, ids):
    """Rebuild the private evidence used by a separately invoked attempt verifier."""
    if not (attempt / "code" / "pdf_identity.py").is_file():
        return
    import drawing_pdf

    limit = 64 * 1024 * 1024
    report = attempt / "README.txt"
    notes = report.read_text(encoding="utf-8") if report.is_file() else ""
    if notes:
        cap = re.search(r"(?:--max-mib\s+|Input size ceiling:\s*)(\d+)", notes)
        if cap and int(cap[1]) > 0:
            limit = int(cap[1]) * 1024 * 1024
    forms = set()
    for path in paths:
        source = root / path
        if path.suffix.lower() != ".pdf" or source.is_symlink():
            continue
        try:
            if source.stat().st_size <= limit:
                forms.update(drawing_pdf.discover(source.read_bytes()).forms)
        except (OSError, ValueError):
            continue
    resolve(forms, ids, paths if PART_REPORT in notes.splitlines() else ())


class Observations(set):
    """Keep recognized people, document title spellings and their ordinary discovery strings."""

    def __init__(self, values=()):
        super().__init__(values)
        self.people = set()
        self.forms = set()

    def observe(self, role, value):
        value = value.strip()
        if role == "person":
            self.people.add(canonical(value))
            parsed = account(value)
            if not parsed:
                return
            principal, login, computer = parsed
            self.add(principal)
            self.forms.add(("account", value, principal))
            self.forms.add(("realm", login, principal))
            if computer and canonical(computer) not in GENERIC and not structural(computer):
                self.add(computer)
        elif role == "title" and (parts := asset(value)):
            folders, stem, _ = parts
            self.add(stem)
            self.update(
                v
                for v in folders
                if v
                and len(v) >= 3
                and v not in (".", "..")
                and canonical(v) not in SYSTEM_FOLDERS
                and not structural(v)
            )
            self.forms.add(("asset", value, ""))

    def finish(self):
        for kind, name, target in tuple(self.forms):
            if kind == "realm" and canonical(name) in self.people:
                self.forms.add(("confirmed", name, target))
        return self


def typed(role, value, alias):
    observe = getattr(alias, "observe", None)
    if observe is not None:
        observe(role, value)
        return alias(value)
    owner = getattr(alias, "__self__", None)
    if canonical(value) in getattr(owner, "replacements", {}):
        return alias(value)
    if role == "person":
        record = account(value)
        if record:
            return alias(record[0])
    if role == "title" and asset(value):
        return asset_title(value, alias)
    return alias(value)


def link_part_names(paths, ids):
    """Keep the mechanical suffix when other files in the same collection confirm a part code."""
    groups = defaultdict(list)
    for path in paths:
        ext = path.suffix.casefold()
        if ext not in MODEL_EXTENSIONS and ext not in {'.dxf', '.pdf'}:
            continue
        stem = path.stem
        scope = path.parts[:3] if len(path.parts) >= 4 and path.parts[0].casefold() == 'sources' else path.parent
        for space in re.finditer(r'\s+', stem):
            prefix, suffix = stem[:space.start()], stem[space.end():]
            words = re.split(r'[\s_-]+', canonical(suffix))
            if (len(words) > 4 or not all(word in PART_WORDS for word in words) or len(prefix) > 55
                    or not PART_CODE.fullmatch(prefix) or STANDARD_CODE.match(prefix)
                    or structural(prefix) or DIMENSION.search(prefix)):
                continue
            groups[scope, canonical(prefix)].append((stem, suffix, ext))
            break
    proposals = defaultdict(set)
    for (_, prefix), records in groups.items():
        forms = {canonical(stem) for stem, _, _ in records}
        extensions = {ext for _, _, ext in records}
        if len(forms) == 1 and not ('.pdf' in extensions and extensions & MODEL_EXTENSIONS):
            continue
        replacement = ids.alias(prefix)
        ids.learn(prefix)
        for stem, suffix, _ in records:
            proposals[canonical(stem)].add(replacement + ' ' + suffix)
    for name, values in proposals.items():
        if len({canonical(value) for value in values}) == 1:
            ids.replacements.setdefault(name, min(values))


def resolve(forms, ids, paths=(), *, link_parts=True):
    domains = defaultdict(set)
    confirmed = defaultdict(set)
    titles = set()
    for kind, raw, target in forms:
        name, root = canonical(raw), canonical(target)
        if kind == "realm":
            domains[name].add(root)
        elif kind == "confirmed":
            confirmed[name].add(root)
        elif kind == "account":
            ids.replacements[name] = ids.alias(root)
        elif kind == "asset":
            titles.add(raw)
    for login, roots in confirmed.items():
        if (
            len(login) >= 3
            and login not in GENERIC
            and not structural(login)
            and len(roots) == 1
            and roots == domains[login]
        ):
            ids.replacements[login] = ids.alias(next(iter(roots)))
    if link_parts:
        link_part_names(paths, ids)
    for title in sorted(titles):
        ids.replacements[canonical(title)] = asset_title(title, ids.alias)
    for spelling in sorted(ids.replacements):
        ids.learn(spelling)
