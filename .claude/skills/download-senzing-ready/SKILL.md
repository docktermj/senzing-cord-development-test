---
name: download-senzing-ready
description: Download every CORD JSONL file listed in manifest.json into the local senzing-ready/ directory, checking each SHA-256 and keeping locally modified files. Use when the user runs /download-senzing-ready, or asks to fetch, sync or get the JSONL data files locally. Writes only to the gitignored senzing-ready/ directory.
argument-hint: "[--force <name>]..."
---

Bring `senzing-ready/` in line with `manifest.json`. The script `download_senzing_ready.py` next to this file
does the work; this skill runs it and reports.

## Rules

- `senzing-ready/` is the gitignored directory at the root of the clone, not `/senzing-ready`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- Never pass `--force` unless the user named that file. A locally modified file may be an edit
  waiting for `/upload-senzing-ready`.
- Change nothing but `senzing-ready/`: not `manifest.json`, `CHANGELOG.md`, `ghcr.io` nor any Release.

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
     - For a **`ghcr.io` failure**, show the printed fix, usually
       `gh auth token | oras login ghcr.io -u <user> --password-stdin`.
   - **Exit 2:** the run stopped before downloading anything. Show the message.

## What the script does

For each manifest entry, in name order:

| `senzing-ready/<name>` | Action |
| --- | --- |
| Missing | Download |
| SHA-256 matches | None (`current`) |
| SHA-256 differs | Keep it (`locally modified`), unless named with `--force <name>` |

A released entry (with a `url`) downloads from its public Release URL with
`curl -fsSL`, with no authentication. An unreleased entry (`url` is `null`) downloads with
`oras blob fetch <oci>@sha256:<sha256>`, which needs `oras login ghcr.io`.

Each download goes to `senzing-ready/.<name>.part`, is checked against the manifest's SHA-256, and is
renamed into place only if it matches. A failed or interrupted download leaves no partial
file, and the remaining entries still download. Files in `senzing-ready/` that aren't in the manifest
are left alone. `--force` with a name that isn't in the manifest stops the run.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/download-senzing-ready -p 'test_*.py'
```

The tests use fake `curl` and `oras` executables, so they need no network.
