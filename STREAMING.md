# Processing a full Machina manifest

Use Python 3.10 or later on Linux or macOS with a trusted HTTPS certificate store. Install the exact format dependencies listed in this source checkout. Use a current organizer manifest from the download mirror and keep the original bytes so the checkpoint can verify its snapshot. The runner reads local verified inputs when available and fetches individual missing files over reusable HTTPS connections to the official mirror. It never creates a raw corpus copy. [Organizer download mirror](https://machina-input.joinbuilders.org/manifest.tsv)

```sh
python3 check_stream.py
python3 stream_corpus.py \
  --manifest /private/machina/manifest.tsv --state /private/machina/checkpoint \
  --cache /mounted/input --attempt /private/machina/staging/attempt-1 \
  --archive /volumes/output/attempt-1
```

The archive is a durable output volume with enough capacity for every emitted file. It must start empty. Omit `--cache` to read only from the mirror or supply `--offline` to use only the cache. Omit `--archive` to hold one sanitized chunk for local inspection. To continue after a chunk fills, rerun with the archive or the official upload destination. A small run fitting in one chunk leaves a complete local attempt if no destination is supplied.

Add `--learn-native` to learn typed author, company, part and project identities from supported SolidWorks and Inventor compound documents. The run reports those native files as omitted because opaque application streams can contain the same private identities. See [the native CAD audit](NATIVE-AUDIT.md).

Install `requirements-verify.txt` and pass `--verify-pdfs` to screen each transformed PDF with independent readers before including it in a chunk. Files that fail are counted in the omission report.

For a drawing-focused practice attempt, run `python3 select_drawings.py full-manifest.tsv drawing-manifest.tsv`. The selection includes every PDF, models with a matching filename stem, exchange models in the same directory as a PDF, and native same-stem files for identity discovery. Pass the selected manifest to the runner. The complete manifest remains the input for a full-corpus attempt.

Each manifest row participates in path and reference resolution. A global first pass records distinct document identities, followed by redaction using the same key and path index across chunks. A transfer or integrity failure leaves its file pending for retry. The runner postpones redaction until all eligible discovery inputs are accounted for. Both passes verify byte size and available organizer MD5. In the absence of organizer MD5, the second pass must match SHA-256 of the first copy.

Each worker may reuse the parsed identity fields of a byte-identical file with the same extension. It reads and checks every file against its own source record first. It keeps at most 64 small discovery records per worker. In a 14-copy STEP group, the initial 89,440,576-byte file took 27.13 CPU seconds to parse. Three separate files in the group produced the same 1,155 identity values after independent hash checks in 0.25, 0.07 and 0.07 CPU seconds.

Both discovery and rewriting use a bounded pool of worker processes. Only the parent writes the checkpoint and staged files. The defaults are four workers, 64 MiB per input, 256 MiB of staged corpus and 1 GiB for the private SQLite database. Its rollback journal may temporarily use up to another database's worth of disk space. Reserve additional space for the final source bundle, omission report, directory entries and filesystem allocation overhead. Raw objects and transformed documents are in RAM. Allow memory for the global identity trie, full path index, concurrent raw inputs and each handler's expanded data. `--reserve-mib` sets the free-space floor; `--chunk-mib`, `--state-mib` and `--workers` can change at resume. Files above the selected input ceiling and unsupported or unparseable formats are counted in the final report. Choose `--max-mib` before starting discovery.

The private state includes the generated key, raw path inventory and learned identities. Keep it private, backed up and separate from submission staging. Supply `--key-file` to reuse a private 32-byte key. Resume with the same manifest, strategy source, attempt staging directory and key. The runner rejects simultaneous writers. After a crash, rerun the command. Output files are checked against the private checkpoint before chunk delivery. Validated local copies or a successful official uploader exit allow local chunk files to be released. Review the final report and submit the completed attempt location only after all chunks and the code have been delivered.

Inspect aggregate counts at any time, including cause and count of pending inputs, without exposing original filenames.

```sh
sqlite3 /private/machina/checkpoint/checkpoint.sqlite3 \
  'SELECT status,reason,count(*) FROM files GROUP BY status,reason;'
```

## Direct delivery to the official team folder

The track page supplies the team's destination, upload token, and organizer `upload.py`. Use a new official attempt folder for this run. Set `MACHINA_UPLOAD_TOKEN` in the operator environment without putting it in shell history or source. Keep the token-renewal file private and refresh it from the track page when the uploader requests renewal. The runner forwards its path to the organizer tool. [Track page and uploader](https://www.joinbuilders.org/buildathon/tracks/machina-deid)

```sh
python3 stream_corpus.py \
  --manifest /private/machina/manifest.tsv --state /private/machina/checkpoint \
  --cache /mounted/input --attempt /private/machina/staging/attempt-1 \
  --upload-to gs://builders-machina-submissions-use1/teams/YOUR-TEAM-ID/attempt-1/ \
  --uploader /private/tools/upload.py --token-file /private/machina/renewal-token
```

Each invocation of the official uploader uses its resume checks. The runner pins its SHA-256, binds the destination to the checkpoint and sends only the staged corpus, then the reviewed source bundle and final report. A failed or interrupted upload keeps the files for another try. The operator verifies the live team folder and submits it in the track interface. The local regression exercises the archive destination without needing cloud credentials.

During discovery, the coordinator shares evidence for identical files across workers. It uses manifest size, format and MD5 to identify candidates. The worker still reads every physical input and verifies its SHA-256 before reusing a result committed in the current pass. A mismatch goes through the normal parser. A paired check on 30 distinct duplicate groups covered 19.3 MB across seven formats and produced identical identity and status evidence.

For a network-limited mirror run, `--prefetch-small 6` overlaps six small transfers with the normal parser workers. The buffer holds at most twelve queued inputs of up to 128 KiB each. Cached and larger inputs follow the existing path. Each received input is checked against the manifest before parsing. On 96 uncached organizer files in four alternating groups, two workers took 27.67 seconds without prefetch and 9.45 seconds with it. Source digests, file dispositions and all identity evidence agreed.

Use `--prefetch-small 6 --prefetch-kib 512` to include files up to 512 KiB in those reads. The twelve queued input payloads can then occupy up to 6 MiB. Two separate groups of 96 uncached files between 128 and 512 KiB took 36.88 and 29.06 seconds with the wider limit. The default limit took 59.39 and 46.63 seconds on the same groups. Both runs produced the same source digests, decisions and identity observations for all 192 paths.

With prefetch enabled, the writer also reads small inputs ahead while it transforms the preceding files. Each worker checks the input size, organizer MD5 and the SHA-256 recorded at discovery. An unavailable or changed source stays pending for retry. Across four alternating batches of 88 uncached inputs, two workers took 47.22 seconds without rewrite prefetch and 36.96 seconds with it. All 88 outputs and reference counts agreed. Separate complete CLI runs matched on 26 further drawings and models, including six PDFs.

When the same input occurs on several paths, a writer can reuse a successfully validated result within the current inventory. Each path still passes the organizer size, MD5 and discovery SHA-256 checks. The writer repeats the transformation for any source that uses a file reference. Each worker retains up to 64 path-independent outputs of at most 512 KiB each. Across alternating trials on 89 actual paths covering ten extensions, elapsed time went from 45.11 to 30.21 seconds. All dispositions, paths, output bytes and reference counts matched. A separate run through the command line produced identical files on 34 additional paths.

## Applying the path resolver to a discovery checkpoint

This release uses the same byte-level discovery as full-manifest release `30bb3bb`. Once the global inventory is built, it shares an owner's pseudonym across that owner's GitHub repositories, retains recognized engineering folders in their path context, and preserves a drawing suffix when a neighboring model confirms its stem.

To carry a partial or complete discovery pass into this release, use a new state and staging folder. Match the original manifest, size limit, native metadata option and PDF verification option.

```sh
python3 fork_discovery.py --source /private/full/checkpoint.sqlite3 \
  --manifest /private/machina/manifest.tsv --state /private/path-release \
  --attempt /private/new-staging/attempt-1 --max-mib 128 --learn-native --verify-pdfs
```

The command takes a read-only SQLite snapshot, checks the original and current parser releases, and initializes an independent private alias key. The snapshot must precede rewriting and delivery. No mirror reads are required. The new database preserves the pending rows, verified source hashes, previously discovered words and typed forms. Run `stream_corpus.py` using the new paths and the same strategy settings to continue discovery or start rewriting. A later snapshot can capture additional progress in a new destination.
