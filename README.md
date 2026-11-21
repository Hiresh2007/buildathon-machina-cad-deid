# Machina CAD De-ID pilot

This two-pass processor pseudonymizes text STEP/STP models and ASCII DXF drawings. It builds the Machina attempt directory with a corpus, runnable source, and a report of every omitted input. The runtime uses Python 3.10+ and its standard library. Development and synthetic checks used Python 3.12 and 3.14. [Official track](https://www.joinbuilders.org/buildathon/tracks/machina-deid)

## Run locally

Put the downloaded documents in a private directory on a case-sensitive filesystem. Choose an output directory with a new attempt number and a separate private key file. The pipeline generates a 32-byte random key at mode 0600 on first use. Reuse that key and the same input snapshot to reproduce an attempt.

```sh
python3 pipeline.py \
  --input /private/work/input \
  --attempt /private/work/output/attempt-1 \
  --key-file /private/work/keys/pseudonyms.bin
```

The output contains:

- `corpus/` with the transformed engineering files under their translated paths.
- `code/` with the source needed to repeat the run and the optional independent CAD reader checks.
- `README.txt` with the run scope, output counts, unresolved-reference counts and a pseudonymous path and reason for each omission.

The program stages the entire attempt before publishing the directory. A new attempt needs a fresh directory name. Each file is processed in memory. The default per-file cap is 64 MiB, adjustable with `--max-mib`. Oversized files appear in the omission report. Keep private inputs, the key and generated attempts outside the code directory.

## What changes

1. Scan supported inputs for identities in typed metadata fields. Learn file stems and identifying directory components. The shared lookup normalizes Unicode, case and whitespace, then derives pseudonyms with HMAC-SHA-256. Email aliases use the reserved `example.invalid` domain. Only the running process holds the lookup.
2. Rewrite STEP author, organization, address, authorization, product, assembly and document identity fields, along with identifying named properties. The scanner understands nested lists, typed values, doubled apostrophes, comments and common STEP Unicode escapes. It preserves quoted strings in their original encoding when unchanged. Comments are removed. It verifies header and data sections, balanced parentheses, unique entity definitions and local entity references before accepting a file.
3. Rewrite DXF authorship properties, named title-block attributes, known identities and contacts in visible text, and paths in supported external-reference records. It locates fields by DXF group code and entity type. Drawing coordinates, handles, dimensions and block references retain their original records.
4. Resolve file references relative to the drawing or by an unambiguous basename from the inventory. Recompute relative links using the translated source and target locations. Preserve common structural names such as `sources/github`, `sources/gitlab`, `drawings`, `models`, and format directories. Other path segments receive pseudonyms. The extension and original case of preserved segments remain intact.

## Checks

```sh
python3 check.py
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

## Coverage limits

This is a subset pilot on the organizer's corpus. Handling of archives, PDFs, images, binary DXF, proprietary CAD files, logos and embedded previews is outstanding. The pipeline omits unsupported files and reports them. STEP exchange files with signed or external-reference sections are outside its supported grammar. The text handlers have limited coverage of embedded formatting, untagged identities, domain aliases and company names split across fields. Some neutral directory and model names can be pseudonymized when they fall outside the preserved vocabulary.

STEP geometry tokens and entity handles are preserved. Recognized material and unit fields are protected. Descriptions and drawing annotations use the identity dictionary plus contact-pattern matching. A match can be ambiguous in free text. An absent, ambiguous or unsupported referenced file can leave a broken external link. Review the report and engineering usability before submitting a complete attempt. No public score has been measured for this pilot.
