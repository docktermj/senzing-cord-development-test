---
name: upload-senzing-ready
description: Push new and changed CORD JSONL files from the local senzing-ready/ directory to ghcr.io, and record them in manifest.json and the [Unreleased] section of CHANGELOG.md. Use when the user runs /upload-senzing-ready. Commits nothing; /release-cord publishes the result.
disable-model-invocation: true
argument-hint: "[--remove <name>]..."
---

Push every `senzing-ready/*.jsonl` file whose SHA-256 differs from `manifest.json`, or that has no
entry, to its own `ghcr.io` repository, then update `manifest.json` and `CHANGELOG.md` in the
working tree. The script `upload_senzing_ready.py` next to this file does the work; this skill runs
it and reports.

## Rules

- `senzing-ready/` is the gitignored directory at the root of the clone, not `/senzing-ready`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- A manifest entry with no file in `senzing-ready/` is left alone. Remove an entry only when the user
  names it in `$ARGUMENTS` with `--remove <name>`. Removal changes the manifest and
  CHANGELOG only, never `ghcr.io` or a Release.
- Never commit, push a branch, create a Release or change package visibility.

## Steps

1. Run the preview from the repository root, passing any `--remove` arguments the user gave:

   ```bash
   python3 .claude/skills/upload-senzing-ready/upload_senzing_ready.py --dry-run $ARGUMENTS
   ```

   Exit 2 means it stopped before changing anything. Show the message, which says how to fix
   it: usually `gh auth refresh -h github.com -s write:packages`, then
   `gh auth token | oras login ghcr.io -u <user> --password-stdin`. Then stop.

2. Show the user the preview: which files would be pushed (added, changed or repaired),
   which are current, and which are refused and why. If nothing would be pushed or removed,
   say so and stop. If any file is refused, ask whether to continue with the rest.

3. Run it for real:

   ```bash
   python3 .claude/skills/upload-senzing-ready/upload_senzing_ready.py $ARGUMENTS
   ```

   Large files take minutes to push. `ghcr.io` times out an upload after 10 minutes.

4. Report each line of the script's summary (`pushed`, `removed`, `current`, `refused` and
   `failed`), then show `git diff -- manifest.json CHANGELOG.md`. Exit 1 means some files
   were refused or failed. Their entries are unchanged, and a rerun retries them.

5. Remind the user that nothing is committed, and that `/release-cord` publishes the
   changes.

## What the script does

| Local file | Action | Manifest | CHANGELOG `[Unreleased]` |
| --- | --- | --- | --- |
| No entry | Push | New entry; `tag` and `url` are `null` | Line under `### Added` |
| SHA-256 differs | Push | `size` and `sha256` updated; `tag` and `url` set to `null` | Line under `### Changed`, or the `### Added` line rewritten |
| SHA-256 matches; `sha256-<hex>` tag missing | Push | `oci` set to the per-file repository | None |
| SHA-256 matches; tag present | None | Unchanged | None |

Each push is
`oras push ghcr.io/<owner>/<repo>/<name>:sha256-<hex> --artifact-type application/vnd.senzing.cord.v1 --annotation org.opencontainers.image.source=https://github.com/<owner>/<repo> <name>:application/x-ndjson`,
run from `senzing-ready/`. The owner and repo come from the `origin` remote. After each push, the
script reads the manifest back and confirms that the only layer is `sha256:<hex>` with the
file's size.

Before a push, each file must:

- have a name that matches `^[a-z0-9][a-z0-9._-]*\.jsonl$`;
- be at most 2 GiB;
- have every line parse as JSON.

`manifest.json` is written once at the end, with the pushes that succeeded, and sorted by
name. `CHANGELOG.md` is created, with the Keep a Changelog header, only when there is a line
to write. `[Unreleased]` holds at most one line per file.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/upload-senzing-ready -p 'test_*.py'
```

The tests run the script against fake `oras` and `gh` executables, so they need no network.
