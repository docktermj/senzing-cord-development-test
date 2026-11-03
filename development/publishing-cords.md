# How to publish CORDs

You need write access to this repository. Without it, see
[Submit a CORD without write access](submit-without-write-access.md).

1. Clone the repository, then run `gh auth login` and `pip install .`.
2. Optional: in Claude Code, run `/download-senzing-ready` to fill `senzing-ready/` with the
   current files. Uploads look only at the files in `senzing-ready/`, so you need just the
   ones you add or replace.
3. Add a new file to `senzing-ready/`, or replace an existing one. Name it
   `<source>-<region>.jsonl`, such as `gleif-london.jsonl`.
   - A new region first needs an entry in `REGIONS` in
     `.claude/skills/upload-senzing-ready/upload_senzing_ready.py`.
   - To remove a file, run `/upload-senzing-ready --remove <name>` instead.
4. Run `/publish-cord`. It uploads the new and changed files, asks which part of the version to
   bump, and opens a `release-<version>` pull request. Approve the merge when it asks, and it
   publishes the Release.

[CORD publishing approach](publishing-approach.md#current-workflow) explains each step
and why the files are published this way.

## Add a `cords.json` entry

Every released file needs an entry in [`cords.json`](cords.json), keyed by file name.
`/publish-cord` stops before releasing a file without one. Add it before running
`/publish-cord`:

```json
"gleif-london.jsonl": {
  "synopsis": "One or two sentences on what the data is.",
  "primary_source": {"name": "GLEIF (Global LEI)", "url": "https://www.gleif.org/"},
  "license": null,
  "snapshot_date": null,
  "data_source": "GLEIF",
  "sample_record": {"DATA_SOURCE": "GLEIF", "RECORD_ID": "...", "...": "..."}
}
```

- `synopsis`, `primary_source` (`name` and `url`), `license` and `snapshot_date`
  (`YYYY-MM-DD`): from the primary source, or `null` where it doesn't say. The catalog
  shows `null` as "Not stated".
- `data_source`: the records' `DATA_SOURCE` code.
- `sample_record`: one record from the file, as JSON; the first line works.
