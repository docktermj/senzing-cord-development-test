---
name: download-senzing-ready
description: Download every CORD JSONL file listed in manifest.json into the local senzing-ready/ directory, checking each SHA-256 and keeping locally modified files. With --verify, it instead checks the released files where they are published (Release asset digests, and the senzing.com copy where there is one) without writing anything. Use when the user runs /download-senzing-ready, asks to fetch, sync or get the JSONL data files locally, or asks to verify or check the released files against manifest.json. Writes only to the gitignored senzing-ready/ directory.
argument-hint: "[--force <name>]... | --verify"
---

Bring `senzing-ready/` in line with `manifest.json`. The script `download_senzing_ready.py` next to this file
does the work; this skill runs it and reports.

## Rules

- `senzing-ready/` is the gitignored directory at the root of the clone, not `/senzing-ready`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- Never pass `--force` unless the user named that file. A locally modified file may be an edit
  waiting for `/upload-senzing-ready`.
- Change nothing but `senzing-ready/`: not `manifest.json`, `CHANGELOG.md` nor any Release.

## Steps

1. Run, from the repository root, passing any `--force <name>` arguments the user gave:

   ```bash
   python3 .claude/skills/download-senzing-ready/download_senzing_ready.py $ARGUMENTS
   ```

   Large files take minutes.

2. Report the summary lines `downloaded`, `current`, `locally modified` and `failed`.
   - **Exit 0:** `senzing-ready/` matches the manifest.
   - **Exit 1:** some entries failed or are locally modified.
     - For a **locally modified** file, say that it was kept. The user can upload it with
       `/upload-senzing-ready`, or overwrite it with `/download-senzing-ready --force <name>`.
     - For an **unreleased file that failed**, show the printed fix, usually `gh auth login`.
   - **Exit 2:** the run stopped before downloading anything. Show the message.

   With `--verify`, report the `verified`, `unreleased, not checked` and `failed` lines
   instead (see [Verify mode](#verify-mode)). Don't try to fix a failure; report it.

## What the script does

For each manifest entry, in name order:

| `senzing-ready/<name>` | Action |
| --- | --- |
| Missing | Download |
| SHA-256 matches | None (`current`) |
| SHA-256 differs | Keep it (`locally modified`), unless named with `--force <name>` |

A released entry (with a `url`) downloads from its public Release URL with
`curl -fsSL`, with no authentication. An unreleased entry (`url` is `null`) downloads from
the `unreleased` staging draft Release with
`gh release download unreleased --pattern <name>`. A draft is visible only to the
repository's writers, so this needs `gh auth login`.

Each download goes to `senzing-ready/.<name>.part`, is checked against the manifest's SHA-256, and is
renamed into place only if it matches. A failed or interrupted download leaves no partial
file, and the remaining entries still download. Files in `senzing-ready/` that aren't in the manifest
are left alone. `--force` with a name that isn't in the manifest stops the run.

## Verify mode

`--verify` checks the published copies instead of downloading into `senzing-ready/`. It
writes nothing there, ignores local files, and doesn't stop for an old `jsonl/` directory.
The `check-releases` GitHub Actions workflow runs it weekly and on demand.

```bash
python3 .claude/skills/download-senzing-ready/download_senzing_ready.py --verify
```

For each manifest entry with a `url`:

- The Release asset's `digest` from `gh release view <tag> --json isDraft,assets` must equal
  `sha256:<sha256>`. The asset isn't downloaded. A missing or draft Release, a missing asset
  and an asset with no digest each fail the entry. `gh` is called once per Release.
- When `source` is set, the file is downloaded from it with `curl -fsSL` into a temporary
  directory, hashed, compared and deleted. A 404 or a different SHA-256 fails the entry.
  An entry with `"source": null` is checked only against its Release asset.

Unreleased entries (`url` is `null`) are listed as `unreleased, not checked`. Each result is a
`verified: <name>: <url>` or `failed: <name>: <url>: <reason>` line naming the file and where
it was checked. Exit 0 means every check passed, 1 that some failed, and 2 that the run stopped
(no `manifest.json`). `--force` can't be combined with `--verify`.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/download-senzing-ready -p 'test_*.py'
```

The tests use fake `curl` and `gh` executables, so they need no network.
