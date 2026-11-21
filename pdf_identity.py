"""Resolve only identity relationships attested by typed PDF person and title fields."""

from collections import defaultdict
from pathlib import PurePosixPath
from urllib.parse import urlsplit
import re

from naming import canonical, engineering_directory, structural


# Only an email address, an HTTP(S) address, or an explicit www host supplies
# evidence that a dotted spelling is a domain. Engineering filenames do not.
CONTACT_REPORT = "Contact domains share pseudonyms across email, web links and visible text."
STEP_CONTACT_REPORT = "STEP header authors and organizations contribute contact domains."
SHEET_REPORT = "Numbered PDF sheet titles preserve inventory-confirmed drawing and model pseudonyms."
SHEET_TITLE = re.compile(
    r"(?P<base>[^/\\\r\n]{1,200}?)"
    r"(?P<suffix>(?:\s*[-–—_:|,;]\s*|\s+)(?:sheet|page|layout|blatt|feuille|hoja|foglio)"
    r"\s*[-.#:]?\s*\d{1,3}(?:\s*(?:/|of|sur|von|de|di)\s*\d{1,3})?)\Z",
    re.I,
)
HOSTNAME = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
HOST_END = r"(?![a-z0-9_-]|\.[a-z0-9])"
HOST_EVIDENCE = re.compile(
    r"(?P<url>(?<![\w.-])(?:https?://|www\.)[^\s<>\x22']+)"
    + r"|(?<![\w.+-])[\w.+-]+@(?P<host>" + HOSTNAME + r")" + HOST_END, re.I | re.ASCII,
)
BARE_HOST = re.compile(r"(?<![\w.-])(" + HOSTNAME + r")" + HOST_END, re.I | re.ASCII)


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

    parts = [part if engineering_directory(part, folders[:i]) else component(part)
             for i, part in enumerate(folders)]
    return "/".join([*parts, component(stem) + extension])


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
    import step

    limit = 64 * 1024 * 1024
    report = attempt / "README.txt"
    contents = report.read_text(encoding="utf-8") if report.is_file() else ""
    use_hosts = CONTACT_REPORT in contents.splitlines()
    use_step = STEP_CONTACT_REPORT in contents.splitlines()
    if contents:
        cap = re.search(r"(?:--max-mib\s+|Input size ceiling:\s*)(\d+)", contents)
        if cap and int(cap[1]) > 0:
            limit = int(cap[1]) * 1024 * 1024
    forms = set()
    for path in paths:
        source = root / path
        extension = path.suffix.lower()
        if source.is_symlink() or (extension != ".pdf" and not (use_step and extension in (".step", ".stp"))):
            continue
        try:
            if source.stat().st_size <= limit:
                reader = drawing_pdf if extension == ".pdf" else step
                observed = reader.discover(source.read_bytes()).forms
                forms.update(item for item in observed if use_hosts or item[0] != "hostname")
        except (OSError, ValueError):
            continue
    resolve(forms, ids, paths if SHEET_REPORT in contents.splitlines() else ())


class Observations(set):
    """Keep recognized people, document title spellings and their ordinary discovery strings."""

    def __init__(self, values=()):
        super().__init__(values)
        self.people = set()
        self.forms = set()

    def observe_hosts(self, text):
        if "." not in text or ("@" not in text and "://" not in text and "www." not in text.casefold()):
            return
        for match in HOST_EVIDENCE.finditer(text):
            host = match["host"]
            if host is None:
                value = match["url"].rstrip(".,;!)")
                try:
                    origin = urlsplit(value if "://" in value else "https://" + value)
                    host = origin.hostname or ""
                except ValueError:
                    continue
                if len(host) > 253 or not re.fullmatch(HOSTNAME, host, re.I | re.ASCII):
                    continue
            host = host.casefold()
            if host.endswith(".example.invalid") or host == "example.invalid":
                continue
            root = host.removeprefix("www.") if host.count(".") >= 2 else host
            self.forms.add(("hostname", root, root))

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
        elif role == "title" and (sheet := SHEET_TITLE.fullmatch(value)):
            base = sheet["base"].strip()
            if base and not structural(base) and not SHEET_TITLE.fullmatch(base):
                self.forms.add(("sheet", value, base))

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


def resolve(forms, ids, paths=()):
    domains = defaultdict(set)
    confirmed = defaultdict(set)
    titles = set()
    sheets = defaultdict(set)
    hosts = set()
    for kind, raw, target in forms:
        name, root = canonical(raw), canonical(target)
        if kind == "hostname":
            hosts.add(name)
        elif kind == "realm":
            domains[name].add(root)
        elif kind == "confirmed":
            confirmed[name].add(root)
        elif kind == "account":
            ids.replacements[name] = ids.alias(root)
        elif kind == "asset":
            titles.add(raw)
        elif kind == "sheet":
            sheets[root].add(raw)
    ids.register_hosts(hosts)
    for login, roots in confirmed.items():
        if (
            len(login) >= 3
            and login not in GENERIC
            and not structural(login)
            and len(roots) == 1
            and roots == domains[login]
        ):
            ids.replacements[login] = ids.alias(next(iter(roots)))
    for title in sorted(titles):
        ids.replacements[canonical(title)] = asset_title(title, ids.alias)
    if sheets:
        # Confirm the isolated base against a real drawing or model filename before
        # changing how any title or matching filename is rendered.
        for path in paths:
            base = canonical(path.stem)
            if base in sheets and ASSET.search(path.name):
                for title in sorted(sheets.pop(base)):
                    suffix = SHEET_TITLE.fullmatch(title)["suffix"]
                    ids.replacements[canonical(title)] = ids.alias(base) + suffix
    for spelling in sorted(ids.replacements):
        ids.learn(spelling)
