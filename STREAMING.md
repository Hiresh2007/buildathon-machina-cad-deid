# Processing a full Machina manifest

Use Python 3.10 or later on Linux or macOS with curl. Install the exact format dependencies listed in this source checkout. Use a current organizer manifest from the download mirror and keep the original bytes so the checkpoint can verify its snapshot. The runner reads local verified inputs when available and fetches individual missing files directly from the official mirror. It never creates a raw corpus copy. [Organizer download mirror](https://machina-input.joinbuilders.org/manifest.tsv)

```sh
python3 check_stream.py
python3 stream_corpus.py \
  --manifest /private/machina/manifest.tsv --state /private/machina/checkpoint \
  --cache /mounted/input --attempt /private/machina/staging/attempt-1 \
  --archive /volumes/output/attempt-1
```

The archive is a durable output volume with enough capacity for every emitted file. It must start empty. Omit `--cache` to read only from the mirror or supply `--offline` to use only the cache. Omit `--archive` to hold one sanitized chunk for local inspection. To continue after a chunk fills, rerun with the archive or the official upload destination. A small run fitting in one chunk leaves a complete local attempt if no destination is supplied.

Each manifest row participates in path and reference resolution. A global first pass records distinct document identities, followed by redaction using the same key and path index across chunks. A transfer or integrity failure leaves its file pending for retry. The runner postpones redaction until all eligible discovery inputs are accounted for. Both passes verify byte size and available organizer MD5. In the absence of organizer MD5, the second pass must match SHA-256 of the first copy.

The defaults are four parallel source reads, 64 MiB per input, 256 MiB of staged corpus and 1 GiB for the private SQLite database. Its rollback journal may temporarily use up to another database's worth of disk space. Reserve additional space for the final source bundle, omission report, directory entries and filesystem allocation overhead. Raw objects and transformed documents are in RAM. Allow memory for the global identity trie, full path index, concurrent raw inputs and each handler's expanded data. `--reserve-mib` sets the free-space floor; `--chunk-mib`, `--state-mib` and `--workers` can change at resume. Files above the selected input ceiling and unsupported or unparseable formats are counted in the final report. Choose `--max-mib` before starting discovery.

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
  --upload-to gs://builders-machina-submissions-1120/teams/YOUR-TEAM-ID/attempt-1/ \
  --uploader /private/tools/upload.py --token-file /private/machina/renewal-token
```

Each invocation of the official uploader uses its resume checks. The runner pins its SHA-256, binds the destination to the checkpoint and sends only the staged corpus, then the reviewed source bundle and final report. A failed or interrupted upload keeps the files for another try. The operator verifies the live team folder and submits it in the track interface. The local regression exercises the archive destination without needing cloud credentials.
