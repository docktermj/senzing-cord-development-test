---
name: upload-senzing-ready
description: Upload new and changed CORD JSONL files from the local senzing-ready/ directory to the "unreleased" draft GitHub Release, and record them in manifest.json and the [Unreleased] section of CHANGELOG.md. Use when the user runs /upload-senzing-ready. Commits nothing; /release-cord publishes the result.
disable-model-invocation: true
argument-hint: "[--remove <name>]..."
---

Upload every `senzing-ready/*.jsonl` file whose SHA-256 differs from `manifest.json`, or that has no
entry, to the draft Release tagged `unreleased` (the staging draft), then update `manifest.json` and `CHANGELOG.md` in the
working tree. The script `upload_senzing_ready.py` next to this file does the work; this skill runs
it and reports.

## Rules

- `senzing-ready/` is the gitignored directory at the root of the clone, not `/senzing-ready`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- A manifest entry with no file in `senzing-ready/` is left alone. Remove an entry only when the user
  names it in `$ARGUMENTS` with `--remove <name>`. Removal changes the manifest and
  CHANGELOG, and deletes the file from the staging draft if it was never released. It never
  touches a published Release.
- Never commit, push a branch, publish a Release, or create one other than the staging draft.

## Steps

1. Run the preview from the repository root, passing any `--remove` arguments the user gave:

   ```bash
   python3 .claude/skills/upload-senzing-ready/upload_senzing_ready.py --dry-run $ARGUMENTS
   ```

   Exit 2 means it stopped before changing anything. Show the message, which says how to fix
   it: usually `gh auth login`. Then stop.

2. Show the user the preview: which files would be uploaded (added, changed or repaired),
   which are current, and which are refused and why. If nothing would be uploaded or removed,
   say so and stop. If any file is refused, ask whether to continue with the rest.

3. Run it for real:

   ```bash
   python3 .claude/skills/upload-senzing-ready/upload_senzing_ready.py $ARGUMENTS
   ```

   Large files take minutes to upload.

4. Report each line of the script's summary (`uploaded`, `removed`, `current`, `refused` and
   `failed`), then show `git diff -- manifest.json CHANGELOG.md`. Exit 1 means some files
   were refused or failed. Their entries are unchanged, and a rerun retries them.

5. Remind the user that nothing is committed, and that `/release-cord` publishes the
   changes.

## What the script does

| Local file | Action | Manifest | CHANGELOG `[Unreleased]` |
| --- | --- | --- | --- |
| No entry | Upload | New entry; `tag` and `url` are `null` | Line under `### Added` |
| SHA-256 differs | Upload | `size` and `sha256` updated; `tag` and `url` set to `null` | Line under `### Changed`, or the `### Added` line rewritten |
| SHA-256 matches; unreleased; staged asset missing or its `digest` differs | Upload (repair) | Unchanged | None |
| SHA-256 matches; released, or staged with a matching `digest` | None | Unchanged | None |

Each upload is `gh release upload unreleased senzing-ready/<name> --repo <owner>/<repo> --clobber`.
The owner and repo come from the `origin` remote. The first upload creates the staging draft
with `gh release create unreleased --draft`. After each upload, the script reads the draft's
assets back and confirms that the asset's `digest` is `sha256:<hex>` of the file.

The script stops before changing anything if a published (non-draft) Release named
`unreleased` exists, or a git tag of that name: the staging draft must stay a draft.
`/release-cord` renames the staging draft to the version it releases, so the next upload
starts a new one.

Before an upload, each file must:

- have a name that matches `^[a-z0-9][a-z0-9._-]*\.jsonl$`;
- have a name of the form `<source>-<region>.jsonl`, where the region (after the last hyphen)
  is a key of `REGIONS` in `upload_senzing_ready.py`. To add a region, add it there with its
  README heading;
- be at most 2 GiB;
- have every line parse as JSON.

`manifest.json` is written once at the end, with the uploads that succeeded, and sorted by
name. `CHANGELOG.md` is created, with the Keep a Changelog header, only when there is a line
to write. `[Unreleased]` holds at most one line per file.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/upload-senzing-ready -p 'test_*.py'
```

The tests run the script against a fake `gh` executable, so they need no network.
