"""Preserve JPEG image data while pseudonymizing EXIF and XMP author fields."""
import json
import subprocess

from pdf_identity import typed

FIELDS = ("IFD0:Artist", "IFD0:XPAuthor", "XMP-dc:Creator")
APP1 = (b"Exif\0\0", b"http://ns.adobe.com/xap/1.0/\0", b"http://ns.adobe.com/xmp/extension/\0")


def scan_offset(image):
    """Return the first scan marker when an APP1 segment contains EXIF or XMP."""
    if not image.startswith(b"\xff\xd8"):
        return None
    at = 2
    tagged = False
    while at + 4 <= len(image):
        if image[at] != 255:
            break
        if image[at + 1] == 255:
            at += 1
            continue
        marker = image[at + 1]
        if marker == 0xDA:
            return at if tagged else None
        if marker == 0xD9:
            break
        if marker == 1 or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = int.from_bytes(image[at + 2:at + 4], "big")
        if length < 2 or at + 2 + length > len(image):
            break
        if marker == 0xE1 and image.startswith(APP1, at + 4):
            tagged = True
        at += 2 + length
    if tagged:
        raise ValueError("invalid JPEG metadata or scan boundary")
    return None


def exiftool(options, image):
    try:
        result = subprocess.run(["exiftool", *options, "-"], input=image,
                                capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("unable to process JPEG metadata with ExifTool") from error
    return result.stdout


def fields(image):
    selected = json.loads(exiftool(["-j", "-s", "-G1", "-api", "StructFormat=JSONQ",
                                    *["-" + key for key in FIELDS]], image))[0]
    return {key: value if isinstance(value, list) else [value]
            for key, value in selected.items() if key in FIELDS}


def transform(image, alias, meaningful, write):
    start = scan_offset(image)
    if start is None:
        return image
    original = fields(image)
    desired = {}
    for key, values in original.items():
        if not all(isinstance(value, str) for value in values):
            raise ValueError("unsupported JPEG author value")
        desired[key] = [typed("person", value.strip(), alias) if meaningful(value) else value
                        for value in values]
    if not write or original == desired:
        return image

    assignments = []
    for key, values in desired.items():
        if original[key] != values:
            assignments.extend(f"-{key}={value}" for value in values)
    updated = exiftool(["-o", "-", *assignments], image)
    end = scan_offset(updated)
    if end is None or fields(updated) != desired or updated[end:] != image[start:]:
        raise ValueError("JPEG author or compressed image verification failed")
    header = updated[:end]
    for key, values in original.items():
        for old, new in zip(values, desired[key]):
            if old != new and len(old.strip()) >= 4 and any(
                old.encode(encoding) in header for encoding in ("utf-8", "utf-16le", "utf-16be")
            ):
                raise ValueError("JPEG metadata contains an additional author occurrence")
    return updated
