"""De-identify standard 3MF core packages and keep native mesh/build references intact."""

from contextlib import contextmanager
import io
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import unquote
import zipfile

from naming import structural


ROOT_MODEL = '3d/3dmodel.model'
CORE_MODEL = re.compile(r'3d/(?:objects/)?(?:3dmodel|model|object|component|part|mesh)[_\d -]*\.model', re.I)
NAMES = {'designer', 'author', 'creator', 'company', 'organization', 'publisher', 'title',
         'project', 'projectname', 'partnumber', 'modelname', 'username'}
FILES = {'sourcefile', 'filename', 'sourcefilename', 'sourcepath', 'filepath'}
IMMUTABLE = {'creationdate', 'modificationdate', 'rating', 'version', 'unit', 'application'}
LIMIT = 64 * 1024 * 1024


def _xml(data):
    from lxml import etree
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('unsupported XML declarations')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True,
                             remove_pis=True, collect_ids=False)
    try:
        return etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError('invalid XML package part') from exc


def _bytes(node):
    from lxml import etree
    return etree.tostring(node, encoding='UTF-8', xml_declaration=True)


def _local(name):
    return name.rsplit('}', 1)[-1]


def _relationship_source(name):
    if name.lower() == '_rels/.rels':
        return ''
    parent, _, leaf = name.rpartition('/_rels/')
    if not parent or not leaf.lower().endswith('.rels'):
        raise ValueError('unsupported relationship part location')
    return posixpath.join(parent, leaf[:-5])


def _resolve(part, target):
    target = unquote(target).split('#', 1)[0]
    if not target or '\\' in target or re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*:', target):
        raise ValueError('unsupported external part target')
    resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else
                                  posixpath.join(posixpath.dirname(part), target))
    if resolved == '..' or resolved.startswith('../'):
        raise ValueError('external package reference')
    return resolved.casefold()


@contextmanager
def _package(data):
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        with archive:
            parts = [i for i in archive.infolist() if not i.is_dir()]
            names = {i.filename.casefold() for i in parts}
            if (len(parts) > 512 or len(names) != len(parts) or sum(i.file_size for i in parts) > LIMIT
                    or any(i.flag_bits & 1 or '\\' in i.filename or i.filename.startswith('/')
                           or '..' in PurePosixPath(i.filename).parts for i in parts)
                    or not {ROOT_MODEL, '[content_types].xml', '_rels/.rels'} <= names):
                raise ValueError('unsupported 3MF layout')
            thumbnails, referenced, rels = set(), set(), {}
            for i in parts:
                lower = i.filename.casefold()
                if lower.endswith('.rels'):
                    node = _xml(archive.read(i))
                    source = _relationship_source(i.filename)
                    if source and not CORE_MODEL.fullmatch(source):
                        raise ValueError('unsupported relationship source')
                    for rel in list(node):
                        if _local(rel.tag) != 'Relationship' or rel.get('TargetMode', '').lower() == 'external':
                            raise ValueError('unsupported relationship')
                        target = _resolve(source, rel.get('Target', ''))
                        if target not in names:
                            raise ValueError('missing package part')
                        if rel.get('Type', '').endswith('/thumbnail'):
                            thumbnails.add(target)
                            node.remove(rel)
                        else:
                            referenced.add(target)
                    rels[i.filename] = node
            if ROOT_MODEL not in referenced or thumbnails & referenced:
                raise ValueError('unsupported start model or shared thumbnail')
            for name in names:
                if name.endswith(('.png', '.jpg', '.jpeg')):
                    if name not in thumbnails or not re.fullmatch(r'(?:metadata/|3d/metadata/)[\w -]+\.(?:png|jpe?g)', name):
                        raise ValueError('unverified texture or raster preview')
                elif not (name == '[content_types].xml' or name.endswith('.rels') or CORE_MODEL.fullmatch(name)):
                    raise ValueError('unsupported vendor settings or attachment')
            yield archive, parts, rels, thumbnails
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise ValueError('unreadable 3MF archive') from exc


def _fields(model):
    if _local(model.tag) != 'model':
        raise ValueError('missing 3MF model root')
    namespace = model.tag.rsplit('}', 1)[0] + '}'
    for node in model.iter(namespace + 'metadata'):
        if len(node):
            raise ValueError('unsupported markup inside 3MF metadata')
        name = re.sub(r'[^a-z0-9]', '', node.get('name', '').split(':')[-1].casefold())
        if not node.text or name in IMMUTABLE:
            continue
        role = 'identity' if name in NAMES else 'path' if name in FILES else 'text'
        yield node, None, role, node.text
    for tag in ('object', 'item'):
        for node in model.iter(namespace + tag):
            for attr in ('name', 'partnumber'):
                if node.get(attr):
                    yield node, attr, 'identity', node.get(attr)


def discover(data):
    with _package(data) as (archive, parts, _, _):
        for member in parts:
            if CORE_MODEL.fullmatch(member.filename):
                model = _xml(archive.read(member))
                for _, _, role, value in _fields(model):
                    if role == 'identity' and not structural(value):
                        yield value


def rewrite(data, alias, redact_text, rewrite_path):
    output = io.BytesIO()
    with _package(data) as (archive, parts, rels, thumbnails), zipfile.ZipFile(output, 'w') as dest:
        for member in parts:
            name = member.filename
            lower = name.casefold()
            if lower in thumbnails:
                continue
            if name in rels:
                body = _bytes(rels[name])
            elif lower == '[content_types].xml':
                node = _xml(archive.read(member))
                for entry in list(node):
                    if entry.get('PartName', '').lstrip('/').casefold() in thumbnails:
                        node.remove(entry)
                body = _bytes(node)
            else:
                model = _xml(archive.read(member))
                for node, attr, role, value in _fields(model):
                    new = (value if structural(value) else alias(value)) if role == 'identity' else (
                        rewrite_path(value) if role == 'path' else redact_text(value))
                    if attr is None:
                        node.text = new
                    else:
                        node.set(attr, new)
                for node in model.iter():
                    for attr in tuple(node.attrib):
                        field = _local(attr).casefold()
                        if field not in {'path', 'thumbnail'}:
                            continue
                        target = _resolve(name, node.get(attr))
                        if target in thumbnails:
                            if field != 'thumbnail':
                                raise ValueError('an engineering resource uses the preview image')
                            del node.attrib[attr]
                body = _bytes(model)
            # Rebuild headers without private ZIP comments or extra fields.
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            dest.writestr(info, body)
    return output.getvalue()
