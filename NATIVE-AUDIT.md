# Native SolidWorks and Inventor metadata audit

`inspect_native.py` reads private files, reports aggregate counts and discards its in-memory experimental edits. Native files remain outside the output handler registry. This audit needs Python 3.10+ and the optional audit dependency `olefile==0.47`.

```sh
python3 -m venv /private/native-audit-env
/private/native-audit-env/bin/pip install olefile==0.47
/private/native-audit-env/bin/python inspect_native.py /private/native-sample
```

The read-only Python function `discover(data)` yields private identity values from supported scalar OLEPS property fields. Callers can pass them to the ordinary private `Identities.learn` inventory. It recognizes author, last editor, manager, company and selected Inventor design-tracking fields, including part number, project, designer, engineer and authority. Original values and filenames are excluded from the CLI report. The audit ignores unknown property sets and limits cross-stream matching to literal whole tokens in raw or bounded zlib-decoded data.

A private sample used five randomly selected files of each extension, restricted to 20,001–1,999,999 bytes with a published MD5. It covered SLDPRT, SLDASM, SLDDRW, IPT, IAM and IDW. All 30 files matched the organizer's size and MD5, totalling 9,143,096 bytes. This size-bounded sample measures the tested files only.

| Observation | Count |
| --- | ---: |
| Microsoft compound-file containers | 19/30 |
| Other binary containers | 11/30 |
| Typed identity fields observed in the compound files | 52 |
| Full aliases that fit and passed the independent property read-back | 4/52 |
| Full aliases exceeding the existing field allocation | 48/52 |
| Compound files with a typed identity repeated in a separate application stream | 19/19 |
| Application streams preserved byte-for-byte during the in-memory trial | 513/513 |
| Native files cleared for the sanitized corpus | 0 |

The probe uses the same HMAC aliases as the production pipeline. It checks storage bounds and typed string lengths, and writes a full alias only when it fits. The remaining fields need a writer that can grow and reallocate property streams. Truncating aliases would break cross-format equality. A separate write to the public metadata fields also leaves proprietary application data, previews, file references, and possible unused container storage to inspect. A second compound-file implementation, SheetJS CFB 1.2.2, independently found the same directory trees, four edited property streams and 513 byte-identical application streams in the 19 containers. Native CAD opening and geometric verification require a compatible CAD reader.

Microsoft documents property types, code pages, container allocation and the standard identity property IDs. [MS-OLEPS](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-oleps/) · [Summary information](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-oleps/f7933d28-2cc4-4b36-bc23-8861cbcd37c4) · [Document summary information](https://learn.microsoft.com/en-us/windows/win32/stg/the-documentsummaryinformation-and-userdefined-property-sets) · [MS-CFB](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/53989ce4-7b05-4f8d-829b-d08d6148375b)

Autodesk exposes Inventor document structure and references through Apprentice. SOLIDWORKS documents third-party data restrictions in modern files and provides its licensed Document Manager API for reference handling. The audit library supports same-size stream replacement. [Autodesk Apprentice](https://help.autodesk.com/cloudhelp/2026/ENU/Inventor-API/files/Apprentice_Overview.htm) · [Autodesk developer guide to iProperties](https://blog.autodesk.io/accessing-iproperties-2/) · [SOLIDWORKS third-party data](https://help.solidworks.com/2025/english/api/sldworksapiprogguide/Overview/Third-party_Data_in_SOLIDWORKS_Files.htm) · [SOLIDWORKS Document Manager](https://help.solidworks.com/2024/english/api/swdocmgrapi/Welcome-swdocmgrapi.html) · [olefile stream writes](https://olefile.readthedocs.io/en/latest/Howto.html)

## Newer SOLIDWORKS files

With `--learn-native`, the streaming first pass also reads document properties from directory-based SLDPRT, SLDASM and SLDDRW files. It validates the central and local headers, the sizes and the CRC of each property stream. It learns the creator, last editor, company and manager from the typed XML fields. Some SOLIDWORKS releases write the last editor under the Dublin Core namespace. The inventory accepts that spelling and omits common software-vendor defaults. The submission report continues to list these native files as omissions while their application streams still require a writer. [Container layout](https://github.com/blussyya/sldprt-format-research/blob/main/format/1-container.md)

A verified sample used 60 parts, 60 assemblies and 60 drawings. The two readers returned identical decompressed bytes for all 360 core and application property streams. Of 56 distinct typed values, 43 were absent from the earlier 19,364-file drawing inventory. Twenty-four of the additional values occurred in several distinct source files. [Independent reader used for verification](https://github.com/blussyya/solidworks-converters/blob/13d8cb28456166a06c0fe0b56113bd6c358b54e2/src/container/modern.js)

## Inventor design-tracking identities

The discovery pass reads the saved, checked-out and checked-in accounts in the Inventor Design Tracking Control property set. It also recognizes checked-by and approval fields, vendor names and catalog URLs in Design Tracking Properties. Account and host observations feed the same identity inventory as drawing metadata. Apache POIFS/HPSF independently decoded all 1,421 compared identity fields in 356 original compound files. The comparison included 319 saved-account entries. [Inventor iProperties](https://help.autodesk.com/cloudhelp/2025/ENU/Inventor-Help/files/GUID-DB4F5723-939F-4034-857C-6D54429ED024.htm) · [Inventor property-set identifiers](https://github.com/TWiesendanger/ExtrabbitCode.Inventor.MetaReader/blob/master/docs/INVENTOR-FILE-FORMAT.md) · [Apache HPSF](https://poi.apache.org/components/hpsf/how-to.html)
