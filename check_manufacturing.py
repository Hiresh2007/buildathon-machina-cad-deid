"""Check identities and native package references in a miniature core 3MF assembly."""

import io
import zipfile
from xml.etree import ElementTree as ET

import manufacturing
from pipeline import Identities


def check():
    model = b'''<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">
    <metadata name="Designer">Orbit Sample</metadata><resources><object id="1" name="Orbit Sample">
    <mesh><vertices><vertex x="1.25" y="2.0" z="3.0"/></vertices><triangles/></mesh>
    </object></resources><build><item objectid="1"/></build></model>'''
    rels = b'''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="r1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
    <Relationship Id="r2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="/Metadata/thumbnail.png"/></Relationships>'''
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('[Content_Types].xml', b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('3D/3dmodel.model', model)
        z.writestr('_rels/.rels', rels)
        z.writestr('Metadata/thumbnail.png', b'private preview placeholder')
    ids = Identities(b'synthetic test')
    for value in manufacturing.discover(archive.getvalue()):
        ids.learn(value)
    cleaned = manufacturing.rewrite(archive.getvalue(), ids.alias, ids, lambda value: value)
    with zipfile.ZipFile(io.BytesIO(cleaned)) as z:
        assert 'Metadata/thumbnail.png' not in z.namelist()
        assert len(ET.fromstring(z.read('_rels/.rels'))) == 1
        tree = ET.fromstring(z.read('3D/3dmodel.model'))
        assert tree.get('unit') == 'millimeter'
        assert tree.find('{*}metadata').text == ids.alias('Orbit Sample')
        assert tree.find('.//{*}object').get('name') == ids.alias('Orbit Sample')
        assert tree.find('.//{*}vertex').attrib == {'x':'1.25', 'y':'2.0', 'z':'3.0'}
        assert tree.find('.//{*}item').get('objectid') == '1'
    assert tuple(manufacturing.discover(cleaned))
    print('3MF typed identities, geometry, units, component references and preview removal passed.')


if __name__ == '__main__':
    check()
