# Machina CAD De-ID

This two-pass processor pseudonymizes text STEP/STP models, ASCII DXF drawings, STL meshes, PDFs, ACIS SAT models, SVG drawings, FreeCAD FCStd projects, standard 3MF core packages and static VRML97 scenes. It builds the Machina attempt directory with a corpus, runnable source, and a report of every omitted input. Most CAD handlers use the Python standard library. The 3MF handler preserves XML namespaces using lxml. PDF processing uses the version of PyMuPDF pinned in requirements.txt. The expanded pipeline and native-reader checks ran on Python 3.12. [Official track](https://www.joinbuilders.org/buildathon/tracks/machina-deid)

## Run locally

Put the downloaded documents in a private directory on a case-sensitive filesystem. Choose an output directory with a new attempt number and a separate private key file. The pipeline generates a 32-byte random key at mode 0600 on first use. Reuse that key and the same input snapshot to reproduce an attempt.

```sh
python3 -m pip install -r requirements.txt
python3 pipeline.py \
  --input /private/work/input \
  --attempt /private/work/output/attempt-1 \
  --key-file /private/work/keys/pseudonyms.bin --workers 4
```

The output contains:

- `corpus/` with the transformed engineering files under their translated paths.
- `code/` with the source needed to repeat the run and the optional independent CAD reader checks.
- `README.txt` with the run scope, output counts, unresolved-reference counts and a pseudonymous path and reason for each omission.

The program stages the entire attempt before publishing the directory. A new attempt needs a fresh directory name. Each file is processed in memory. The default per-file cap is 64 MiB, adjustable with `--max-mib`. Oversized files appear in the omission report. Keep private inputs, the key and generated attempts outside the code directory.

## What changes

1. Scan supported inputs for identities in typed metadata fields. Learn file stems and identifying directory components. The shared lookup normalizes Unicode, case and whitespace, then derives pseudonyms with HMAC-SHA-256. Email aliases use the reserved `example.invalid` domain. The directory processor holds the lookup in memory. The streaming runner keeps its checkpoint in a private directory.
2. Rewrite STEP author, organization, address, authorization, product, assembly and document identity fields, along with identifying named properties. The scanner understands nested lists, typed values, doubled apostrophes, comments and common STEP Unicode escapes. It preserves quoted strings in their original encoding when unchanged. Comments are removed. It verifies header and data sections, balanced parentheses, unique entity definitions and local entity references before accepting a file.
3. Rewrite DXF authorship properties, named title-block attributes, known identities and contacts in visible text, and paths in supported external-reference records. It locates fields by DXF group code and entity type. Drawing coordinates, handles, dimensions and block references retain their original records.
4. Resolve file references relative to the drawing or by an unambiguous basename from the inventory. Recompute relative links using the translated source and target locations. Preserve common structural names such as `sources/github`, `sources/gitlab`, `drawings`, `models`, and format directories. Other path segments receive pseudonyms. The extension and original case of preserved segments remain intact.
5. For binary STL, retain the complete facet table, normals, per-facet attribute bytes and recognized header color/material fields. For text STL, validate facet syntax and rewrite solid names while preserving every triangle record.
6. For PDFs, pseudonymize document information, XMP, recognized title-block fields, visible text, comment authors, bookmark titles and supported file references. The writer physically removes matching glyphs from the content stream and inserts fitted replacements at the same location. It rewrites supported embedded STEP, DXF and STL files, including file-attachment annotations. It rewrites encoded page-label prefixes in their native number trees and preserves numbering styles and ranges. The rebuilt PDF drops stale page thumbnails, unreachable objects and application-private PieceInfo payloads, which can contain a complete older copy of the artwork. The PDF painting resources stay available to drawing viewers.

7. Translate SVG authorship metadata, titled annotations and external file links while preserving element IDs, drawing paths, coordinate attributes and internal anchors.
8. Translate ACIS SAT length-prefixed strings in supported metadata and reference attributes and preserve the B-rep records. In FreeCAD FCStd archives, process document XML properties and embedded SVGs while preserving ZIP member names, application object IDs and shape streams.

9. Read static VRML97 nodes and their typed fields. Redact identifying scene, viewpoint and visible text labels. Remove comments, rename connected DEF/USE symbols, and translate local scene links and viewpoint fragments together. Numeric geometry, topology, transforms, material values and navigation modes retain their original source tokens. Scenes with scripts, event routes, prototypes, unknown fields, encoded textures, VRML 1.0 or external URLs enter the omission report. Geometry that itself depicts an identity requires visual review. [VRML97 grammar](https://www.web3d.org/documents/specifications/14772/V2.0/part1/grammar.html) · [Node reference](https://www.web3d.org/documents/specifications/14772/V2.0/part1/nodesRef.html)

For a full manifest, follow [the streaming instructions](STREAMING.md). The private checkpoint lets you resume a disk-bounded run after interruption. The directory processor accepts `--workers` to distribute individual files between processes. Both passes use one shared set of learned identities.

The first eight-format practice cohort used a deterministic selection with same-directory neighbors. To reconstruct the exact 5,415 paths from the original manifest and a private source mirror, run the following first. Use the resulting practice folder as the directory processor input.

```sh
python3 select_practice.py --manifest /private/work/manifest.tsv --input /private/work/mirror --output /private/work/practice
```


## Checks

```sh
python3 check.py
python3 check_formats.py
python3 check_pdf_identity.py
python3 check_exchange.py
python3 check_manufacturing.py
python3 check_vrml.py
python3 check_stream.py
python3 check_formats.py
```

This synthetic check covers cross-format identities, a two-way assembly link, escaped names, material values, dimensions, stable output across reruns, and reporting of malformed or unsupported files.

For a geometry comparison, install the optional native Open CASCADE reader in a separate environment. These commands use the version used for the pilot.

```sh
python3.12 -m venv /private/work/geometry-env
/private/work/geometry-env/bin/python -m pip install 'cadquery-ocp-novtk==8.0.1.1.0'
/private/work/geometry-env/bin/python check_geometry.py \
  --input /private/work/input \
  --attempt /private/work/output/attempt-1 \
  --key-file /private/work/keys/pseudonyms.bin
```

On 2,050 additional organizer STEP/STP inputs, a faster scanner made the same 2,020 acceptances and 30 omissions as the earlier scanner. Every accepted output was byte-identical. Measured scanner CPU time fell from 485.88 to 151.95 seconds for those inputs.

All twelve real STEP/STP pilot inputs and outputs opened and transferred in Open CASCADE. Root, solid, face, edge and vertex counts matched for each pair, as did bounds, surface area and volume within the check's numeric tolerance. One input emitted a native unresolved-reference warning that also occurred on its transformed counterpart.

An independent DXF comparison uses `ezdxf==1.4.5`. Install it in the validation environment and run `check_dxf.py` with the same three arguments. Seven of the eight pilot inputs and outputs both opened. Their 583 modelspace entities matched by type, and the numeric and handle records matched exactly. The eighth source drawing lacked a required subclass and produced the same native error after processing. A synthetic title block, author field and contact string also passed a separate ezdxf round trip.

The expanded private validation used 71 PDFs and 46 STL meshes across two cohorts. All 129 PDF pages rendered with PDFium. The checks covered 50,810 vector objects, 372 image objects, and 52 instances of dimensions with explicit units. The checker found none of the 83 tested non-neutral author/title values in extracted text, PDF dictionaries or decompressed streams. Three original PDFs had damaged cross-reference tables that pypdf rejected. Their processed versions opened. Trimesh compared 272,140 facets across 38 binary and eight ASCII meshes. Vertex coordinates, face indices and normals matched in each pair.

Install the verifier dependencies to compare your private attempt. Its geometry and metadata checks print aggregate counts and use output paths for verification exceptions.

```sh
python3 -m pip install -r requirements-verify.txt
python3 -m unittest test_pdf_fidelity
python3 check_native_formats.py --input /private/work/input --attempt /private/work/output/attempt-1 --key-file /private/work/keys/pseudonyms.bin
```

The PDF checker compares page dimensions, ordered path commands and styles, clipping, native image hashes and placement. If an edited label overlaps a disputed vector bound, PDFium renders the complete graphics plane while preserving text clipping. The comparison requires agreement within two intensity levels per color channel or an exact second render with path smoothing disabled. The privacy check decodes PDF string objects and scans uncompressed streams for the original author and document title. A failed check omits the document from the validated selection.

On private real inputs, all 549 accepted SVGs preserved the inspected geometry attributes and IDs. Of 1,050 SAT files, 1,048 passed structural checks. In an independent ACIS reader, 56 of the first 80 original files parsed, and all 56 transformed files had matching B-rep entity types and body counts. The other 24 original files were rejected by that reader. All 45 FreeCAD archives reopened and all 1,174 embedded B-rep shape streams matched byte-for-byte. Unrecognized metadata and raster previews in these formats may still contain identifying information.

For a drawing whose path coordinates were rounded during text redaction, the processor can restore numeric coordinates when every path instruction and transformation agrees and each coordinate differs by at most 0.06 user-space units. It keeps the sanitized text and links, then reruns the complete geometry and privacy checks. This recovered two of twelve previously withheld drawings with 5,848 vector objects. All 65 accepted controls remained byte-identical.

The submitted practice cohort uses a second PDF verifier after the directory run. Install `requirements-verify.txt` and use `screen_pdfs.py` with `--input`, `--attempt`, `--key-file`, and a separate private `--holdback` directory. It records held-back outputs in the attempt report.

The 3MF handler preserves mesh vertices, triangle indices, build transforms, object identifiers and units. It pseudonymizes typed model metadata and removes optional thumbnail parts and their relationships. It omits unsupported vendor settings, external resources and unknown attachments. In a sample of 150 real packages, 113 qualified, with 1,367,586 vertices and 2,119,934 triangles unchanged. Of 20 checked in lib3mf 2.5.0, seven source packages opened and matched the rewritten mesh and build state. The native reader rejected the other 13 originals. [3MF Core Specification](https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md)

A GitHub source directory can encode an owner and project in a single component, such as `sources/github/ExampleLab__PumpDesign`. The processor pseudonymizes the two fields separately and retains the double underscore. The same owner therefore keeps its alias across projects, including in drawing titles and relative file references. In the drawing inventory, 12,813 paths matched this form, including 5,856 PDFs. All 24 owners with multiple repositories retained one alias, and all 986 relative-reference probes resolved. Conventional artifact folders directly below a recognized project kept their original names on 167 paths, including 122 PDFs. The owner and project stayed pseudonymized on every path.

## Coverage limits

This is a subset pilot on the organizer's corpus. Standalone archives, raster images, binary DXF, proprietary CAD formats, logos and engineering previews require additional handlers. The pipeline omits unsupported files and reports them. STEP exchange files with signed or external-reference sections are outside its supported grammar. The text handlers have limited coverage of embedded formatting, untagged identities, domain aliases and company names split across fields. The path mapper preserves recognized engineering descriptors, mechanical dimensions and organizer source categories. Unrecognized neutral names can still receive pseudonyms.

STEP geometry tokens and entity handles are preserved. Recognized material and unit fields are protected. Descriptions and drawing annotations use the identity dictionary plus contact-pattern matching. A match can be ambiguous in free text. An absent, ambiguous or unsupported referenced file can leave a broken external link. Review the report and engineering usability before submitting a complete attempt. The first 5,303-file practice submission scored 37 of 1,000. It contained 522 verified PDFs from the 10,342-file PDF collection. Later changes are evaluated separately.

PDF image pixels, outlined lettering, logos and ambiguous unlabelled names can retain identifiers. The handler omits encrypted, signed, interactive, active-script and associated-file documents, and documents containing unsupported attachment formats or rich-text annotation payloads. Supported embedded files share identity mappings with standalone files. These checks cover the tested metadata, engineering records and fixtures. They do not establish complete de-identification of arbitrary PDFs.

### VRML geometry check

The organizer manifest lists 3,551 WRL files. In a private 150-file sample, all 146 distinct input size and MD5 pairs matched that manifest. The parser accepted 63 static VRML97 models. VTK 9.5.2 independently produced identical point arrays, topology, actor transforms and material values from the inputs and outputs, across 71,521 points and 136,115 cells. The other 87 included VRML 1.0 extension scenes, interactive files and unsupported or malformed documents. Synthetic cases cover linked scene files, viewpoints, shared geometry and format-required strings. [VTK VRML importer](https://vtk.org/doc/nightly/html/classvtkVRMLImporter.html)

```sh
python -m pip install -r requirements-verify.txt
python check_vrml.py --vtk
python check_vrml.py --sample-dir /private/work/wrl --manifest /private/work/manifest.tsv
```

### Identity consistency across PDF drawings

A part code can appear before a mechanical description in an engineering filename. When drawings and models in one collection corroborate the code, the processor gives the code a common pseudonym and retains the descriptor. In the drawing inventory, 623 paths used this mapping, including 277 PDFs. Of 36 sampled drawings, 32 passed independent checks across 62 pages, 50 dimensions and 2,804 vector objects. All 30 accepted drawings whose original title matched the filename retained that match. The other four drawings were withheld by the usual verification checks.

Typed authorship fields can include a Windows domain and login followed by a workstation in parentheses. The qualified account keeps its pseudonym when the workstation changes. A bare login is associated with that account only when both spellings occur in person fields in the same document and the corpus contains a single qualifying domain. Multiple domains retain separate pseudonyms. Shared system logins keep their unqualified representation. The private checkpoint stores these observations before the rewrite pass.

Some PDF filenames append a drawing descriptor or CAD extension to the model stem. When the corresponding model exists in the same directory, the drawing retains both its model pseudonym and its descriptor. In 89 such drawing paths the previous processor retained that relationship once and the updated processor retained it 89 times. Eighty-eight drawings also passed the full PDF checks, covering 114 pages, 28,280 vector objects, 131 images and 50 dimensions.

Drawing discovery recognizes numbered streets in labelled address fields or near a country-specific postal region in the same title block. The same spelling receives the same pseudonym in other drawings. Street suffix punctuation, engineering annotations, and dimension text remain available for the ordinary rendering checks.

Conventional engineering directories retain their names both on disk and in typed CAD paths. The same words in repository-owner, project and operating-system account positions keep their pseudonyms. In the drawing manifest, 815 checked directory occurrences across 526 qualifying PDF paths and other model files retained their original spelling. All 55 provider-identity occurrences stayed pseudonymized, 800 relative-reference checks resolved and all 19,364 output paths were distinct. Independent checks on 30 drawings covered 63 pages, 13,007 vector objects, 122 images and 19 dimensions.

CAD filenames in typed PDF titles and explicit visible file labels use the same stem pseudonym as their corresponding models. Standalone CAD filenames and absolute or explicitly relative CAD paths on their own text line are also recognized. Extensions, engineering folder names and drive designators are retained. Private path components are pseudonymized separately. A synthetic two-worker check covers these spellings in PDF metadata and page text and verifies byte-identical results after checkpoint resume.

A targeted audit of 114 original drawings covered 71 qualified accounts. Whole-field hashes produced multiple pseudonyms for 12 of those accounts. The updated processor gave each account one pseudonym and kept all 71 distinct. All 15 CAD-title cases contained the expected model-stem pseudonym. Independent rendering and metadata checks passed for all 131 pages, covering 57,307 vector objects and 186 images.

In a separate targeted sample, 29 drawings passed the independent graphics and metadata checks. They contained 43 identifying visible CAD filenames outside the typed author or title. Every expected model-filename pseudonym appeared after processing. The earlier version left 38 of the original strings visible. The updated output contained none of them. Three additional drawings fell outside the accepted verification set.

Drawing title blocks also use aligned cells for the author, checker, company and drawing number. The processor recognizes abbreviated and multilingual labels and requires one clearly aligned value. For a wholly numeric drawing number, the source document title must confirm the identifier. A paired check on 64 accepted drawings found 74 such values. All 74 expected aliases were visible after processing, compared with three before. Independent readers checked 90 pages, 59,919 vector objects and 53 dimension measurements. Six additional drawings did not pass all checks and were omitted from these counts.

Email addresses and website links supply evidence for a shared domain spelling. The processor reuses that domain's pseudonym in other contact fields and in bare mentions, while keeping different mailboxes distinct. On 45 accepted drawings, all 67 independently identified drawing and domain pairs contained the expected domain pseudonym. The verifier checked 187 pages and 30,868 vector objects. A separate 17-drawing sample matched all 48 expected pairs. In two drawings with links near rewritten text, all 11 checked drawing links retained the expected pseudonymous target. The earlier output retained seven. Inputs labeled PDF with a different internal file format are recorded as omissions.

The same domain mapping also covers email and website addresses in STEP header author and organization fields. A check of 2,050 real models found 22 headers with explicit contact domains. All 22 contained the expected pseudonym after processing. The corresponding DATA sections, totaling 11,705,506 bytes, were byte-identical to the preceding build. A two-worker fixture also checks a bare domain in a PDF whose only identifying contact appears in its companion STEP model.


### Authors embedded in JPEG images

For tagged JPEGs embedded directly in a PDF, install ExifTool 13.55 and put its `exiftool` executable on `PATH`. The processor reads the native EXIF Artist and XPAuthor fields and the XMP Dublin Core creator list. Each contributor uses the same person pseudonym as elsewhere in the drawing. The writer reads the fields back and compares the original and updated compressed picture scans byte for byte. Independent PDF verification also checks the decoded image pixels and placement. Multi-stage PDF image filters and pixel-level recognition of photographic content remain outside this handler’s coverage. Run `python3 -m unittest test_jpeg_authors` to check a multi-author image, a drawing dimension and retained calibration metadata.

In an audit of 10 source drawings, ExifTool read back the expected aliases for seven distinct authors in 26 JPEGs. Their compressed picture scans remained byte-identical. Independent readers confirmed the image placements, 49,959 vector objects and 145 measured dimensions across 789 pages.

When PDFium changes only the extracted whitespace between a number and its unit, the verifier compares every visible digit, decimal mark and unit glyph using its physical character box. It requires the same case, the same number of occurrences and matching box coordinates within 0.0001 PDF point. Two previously withheld drawings passed this check on their existing sanitized bytes. A third drawing with a missing measurement remained withheld.

The PDF metadata pass follows the cross-reference table and checks both ordinary objects and compressed objects. It skips slots marked free or unset. In 78 previously unreadable source drawings, this recovered 21 distinct PDFs representing 34 file paths. Independent readers checked 238 pages, 193,378 vector objects, 841 images and 138 dimensions in those PDFs. A separate comparison on 92 previously accepted drawings returned the same 264 identity observations and 111 typed spelling relationships. [MuPDF cross-reference entry types](https://github.com/ArtifexSoftware/mupdf/blob/1.26.5/include/mupdf/pdf/xref.h)


### Corporate names in drawing ownership notices

The PDF reader uses the value after a printed field caption to identify the drafter, company or drawing number. For example, it keeps “Designer”, “Empresa” and “Zeichnungsnummer” on the page and replaces their identifying values. Empty fields, placeholders, standard identifiers and dimension labels retain their original text.

The PDF reader recognizes named corporate holders after a copyright symbol, a copyright date, “property of” and “proprietary to.” It learns the company across documents while retaining the printed date and legal wording. A registered abbreviation such as “Inc.” can use the same identity with or without its final period. A sentence that names a manufacturer or person outside these contexts follows the ordinary field rules.

In a check of 24 source drawings accepted by both builds, PDFium found all 25 expected company pseudonyms. Five original holder names had survived in the preceding output. None remained visible in those checked occurrences. All 18 checked legal phrases survived, including two occurrences of “property of” that the earlier build removed. Six other source or output pairs failed the separate drawing checks and are excluded from these counts. Run `python3 -m unittest test_ownership_notices` to check dates, legal text, dimensions, independent contacts, and abbreviated suffixes.
