# Machina CAD De-ID

This two-pass processor pseudonymizes text STEP/STP models, ASCII DXF drawings, STL meshes, PDFs, ACIS SAT models, SVG drawings and FreeCAD FCStd projects. It builds the Machina attempt directory with a corpus, runnable source, and a report of every omitted input. The CAD handlers use the Python standard library. PDF processing uses the version of PyMuPDF pinned in requirements.txt. The expanded pipeline and native-reader checks ran on Python 3.12. [Official track](https://www.joinbuilders.org/buildathon/tracks/machina-deid)

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
6. For PDFs, pseudonymize document information, XMP, recognized title-block fields, visible text, comment authors, bookmark titles and supported file references. The writer physically removes matching glyphs from the content stream and inserts fitted replacements at the same location. It rewrites supported embedded STEP, DXF and STL files, including file-attachment annotations. It rewrites the full document and removes unreachable objects and stale page thumbnails. Non-identifying vector drawing data and raster images remain in the PDF.

7. Translate SVG authorship metadata, titled annotations and external file links while preserving element IDs, drawing paths, coordinate attributes and internal anchors.
8. Translate ACIS SAT length-prefixed strings in supported metadata and reference attributes and preserve the B-rep records. In FreeCAD FCStd archives, process document XML properties and embedded SVGs while preserving ZIP member names, application object IDs and shape streams.

For a full manifest, follow [the streaming instructions](STREAMING.md). The private checkpoint lets you resume a disk-bounded run after interruption. The directory processor accepts `--workers` to distribute individual files between processes. Both passes use one shared set of learned identities.

The first eight-format practice cohort used a deterministic selection with same-directory neighbors. To reconstruct the exact 5,415 paths from the original manifest and a private source mirror, run the following first. Use the resulting practice folder as the directory processor input.

```sh
python3 select_practice.py --manifest /private/work/manifest.tsv --input /private/work/mirror --output /private/work/practice
```


## Checks

```sh
python3 check.py
python3 check_formats.py
python3 check_exchange.py
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

All twelve real STEP/STP pilot inputs and outputs opened and transferred in Open CASCADE. Root, solid, face, edge and vertex counts matched for each pair, as did bounds, surface area and volume within the check's numeric tolerance. One input emitted a native unresolved-reference warning that also occurred on its transformed counterpart.

An independent DXF comparison uses `ezdxf==1.4.5`. Install it in the validation environment and run `check_dxf.py` with the same three arguments. Seven of the eight pilot inputs and outputs both opened. Their 583 modelspace entities matched by type, and the numeric and handle records matched exactly. The eighth source drawing lacked a required subclass and produced the same native error after processing. A synthetic title block, author field and contact string also passed a separate ezdxf round trip.

The expanded private validation used 71 PDFs and 46 STL meshes across two cohorts. All 129 PDF pages rendered with PDFium. The checks covered 50,810 vector objects, 372 image objects, and 52 instances of dimensions with explicit units. The checker found none of the 83 tested non-neutral author/title values in extracted text, PDF dictionaries or decompressed streams. Three original PDFs had damaged cross-reference tables that pypdf rejected. Their processed versions opened. Trimesh compared 272,140 facets across 38 binary and eight ASCII meshes. Vertex coordinates, face indices and normals matched in each pair.

Install the verifier dependencies to compare your private attempt. Its geometry and metadata checks print aggregate counts and use output paths for verification exceptions.

```sh
python3 -m pip install -r requirements-verify.txt
python3 check_native_formats.py --input /private/work/input --attempt /private/work/output/attempt-1 --key-file /private/work/keys/pseudonyms.bin
```

On private real inputs, all 549 accepted SVGs preserved the inspected geometry attributes and IDs. Of 1,050 SAT files, 1,048 passed structural checks. In an independent ACIS reader, 56 of the first 80 original files parsed, and all 56 transformed files had matching B-rep entity types and body counts. The other 24 original files were rejected by that reader. All 45 FreeCAD archives reopened and all 1,174 embedded B-rep shape streams matched byte-for-byte. Unrecognized metadata and raster previews in these formats may still contain identifying information.

The submitted practice cohort uses a second PDF verifier after the directory run. Install `requirements-verify.txt` and use `screen_pdfs.py` with `--input`, `--attempt`, `--key-file`, and a separate private `--holdback` directory. It records held-back outputs in the attempt report.

## Coverage limits

This is a subset pilot on the organizer's corpus. Standalone archives, raster images, binary DXF, proprietary CAD formats, logos and engineering previews require additional handlers. The pipeline omits unsupported files and reports them. STEP exchange files with signed or external-reference sections are outside its supported grammar. The text handlers have limited coverage of embedded formatting, untagged identities, domain aliases and company names split across fields. The path mapper preserves recognized engineering descriptors, mechanical dimensions and organizer source categories. Unrecognized neutral names can still receive pseudonyms.

STEP geometry tokens and entity handles are preserved. Recognized material and unit fields are protected. Descriptions and drawing annotations use the identity dictionary plus contact-pattern matching. A match can be ambiguous in free text. An absent, ambiguous or unsupported referenced file can leave a broken external link. Review the report and engineering usability before submitting a complete attempt. No public score has been measured for this pilot.

PDF image pixels, outlined lettering, logos and ambiguous unlabelled names can retain identifiers. The handler omits encrypted, signed, interactive, active-script and associated-file documents, and documents containing unsupported attachment formats or rich-text annotation payloads. Supported embedded files share identity mappings with standalone files. These checks cover the tested metadata, engineering records and fixtures. They do not establish complete de-identification of arbitrary PDFs.
