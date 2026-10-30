---
name: publish-cord
description: Publish a SemVer release of the CORD JSONL files — a GitHub Release holding the new and changed files in manifest.json, and the manifest.json, CHANGELOG.md, README.md and CORD_stats.xlsx updates merged into main. Use when the user runs /publish-cord; it runs the /upload-senzing-ready upload itself first. Asks for the SemVer bump and before merging.
disable-model-invocation: true
---

Publish the files in `manifest.json` as a new release. The script `publish_cord.py` next
to this file does each step. This skill runs it and asks the user the two questions it can't
answer: the SemVer bump, and whether to merge.

## Rules

- Versions are bare SemVer (`0.2.0`), never `v0.2.0`. The old `v0.1.0` counts as `0.1.0`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- Never merge without the user's explicit yes in step 5, in this run.
- Never delete a draft Release, branch or tag. When a step stops, leave things as they
  are; a rerun resumes from the draft.

## Steps

1. Run, from the repository root:

   ```bash
   python3 .claude/skills/publish-cord/publish_cord.py upload
   ```

   This runs `/upload-senzing-ready` without asking: it prints a `preview:` of what it would
   upload, then uploads new and changed `senzing-ready/` files to the `unreleased` staging
   draft Release and records them in `manifest.json` and `[Unreleased]`. Large files take
   minutes.
   - **`skipped: resuming draft <version>`:** a release is already in progress; go on.
   - **Exit 1:** show the `refused:` and `failed:` lines and stop. A refused file was found
     in the preview, so nothing was uploaded; a failed upload leaves the other files
     uploaded, and a rerun retries it.
   - **Exit 2:** show the message, usually the `gh auth login` fix, and stop.

   Removing a file is not part of a release: run `/upload-senzing-ready --remove <name>`
   first.

2. Run, from the repository root:

   ```bash
   python3 .claude/skills/publish-cord/publish_cord.py status
   ```

   - **Exit 2:** show each `blocking:` line and stop.
     - "Nothing to release" means nothing in `senzing-ready/` is new or changed.
     - "not in draft Release … with a matching digest" means rerunning
       `/upload-senzing-ready` with the file in `senzing-ready/`.
     - "region … has no README heading" means adding the region to `REGIONS` in
       `upload_senzing_ready.py`; "expected `<source>-<region>.jsonl`" means renaming the file.
     - "needs openpyxl" means running `pip install openpyxl`, or `pip install .` from the
       repository root.
   - **`draft: <version> (resume)`:** say so, skip the bump question, and go to the step that
     `next:` names (`prepare` is step 4, `publish` is step 5).

3. Ask the user which part of the version to bump: Major, Minor or Patch. Show the version
   each choice gives (the `major:`, `minor:` and `patch:` lines) and recommend the
   `suggested bump:`. It is Major if `[Unreleased]` has Removed lines, Minor if it has Added
   lines, and Patch otherwise. Wait for the answer.

4. Run, with the chosen bump:

   ```bash
   python3 .claude/skills/publish-cord/publish_cord.py prepare --bump <major|minor|patch>
   ```

   This step:
   - renames the `unreleased` staging draft to the draft Release `<version>`, so the new and
     changed files (`tag: null` in `manifest.json`) move with it without being uploaded again;
   - deletes any asset of that draft that isn't a new or changed file, and stops if a new or
     changed file is missing or its `digest` doesn't match `manifest.json`;
   - runs `bin/get_cord_stats.py` on those same files to update `CORD_stats.xlsx`, taking each
     from `senzing-ready/` when its SHA-256 matches and from the draft otherwise;
   - commits `manifest.json`, `CHANGELOG.md`, `README.md` and `CORD_stats.xlsx` on the
     branch `release-<version>`;
   - pushes the branch and opens the PR.

   Report the `pull request:` URL. Exit 2 means it stopped; show the message. A rerun (go back
   to step 1) resumes the draft.

5. Show the PR and ask: "Merge `<PR URL>` with `--merge --delete-branch`, then publish
   `<version>`?" Wait for an explicit yes.
   - **No:** say that the draft Release and the branch are left in place, and that
     `/publish-cord` resumes later. Stop.
   - **Yes:** run:

     ```bash
     python3 .claude/skills/publish-cord/publish_cord.py publish
     ```

     This step merges the PR, publishes the draft on the merge commit as the latest Release, switches the clone to an
     updated `main`, and downloads each file in the Release to check its SHA-256.

6. Report the merge commit, the `published:` line, and each `verified:` line.
   Exit 1 means a download didn't match `manifest.json`: list the `MISMATCH:` lines and
   leave the Release as it is for the user to look at. Exit 2 means it stopped before
   publishing, for example because branch protection refused the merge. Show the message;
   the draft is still unpublished.

## What a release changes

- **Release:** `<version>`, the former `unreleased` staging draft, holding only the files
  that are new or changed since the last release. Its notes are exactly
  `See [CHANGELOG.md](https://github.com/<owner>/<repo>/blob/main/CHANGELOG.md#<anchor>)`.
  `<anchor>` is GitHub's anchor for `[<version>] - YYYY-MM-DD`: brackets and dots dropped,
  spaces turned to hyphens, so `[0.2.0] - 2026-10-28` gives `020---2026-10-28`.
- **`manifest.json`:** each new and changed entry gets `tag` `<version>` and
  `url` `https://github.com/<owner>/<repo>/releases/download/<version>/<name>`. Unchanged
  entries keep the `tag` and `url` of the Release where they last changed.
- **`README.md`:** the file lists under `## CORDs` are rewritten from `manifest.json`. There
  is one `###` section per heading in `REGIONS` in `upload_senzing_ready.py`, in alphabetical
  order; regions that share a heading (`london` and `london_central_a` are both `London`)
  share one section. Each file sits under the heading of the region after the last hyphen in
  its name, sorted by name, and links to its `url`. A heading with no files shows
  "Not published yet." once. The text before the first `###` and everything outside
  `## CORDs` is kept.
- **`CORD_stats.xlsx`:** `bin/get_cord_stats.py` rewrites the row of each new and changed
  file, matched on `SOURCE` and `GEO`. It sets the counts (a feature no longer present becomes
  0), `LAST_UPDATED` (the release date) and `UPDATE_REASON` (`Added in <version>` or
  `Changed in <version>`, following `[Unreleased]`). A new file gets a new row. No other cell
  changes.
- **`CHANGELOG.md`:** `## [Unreleased]` becomes `## [<version>] - YYYY-MM-DD` under a new
  empty `## [Unreleased]`. The link definitions get `[Unreleased]` comparing
  `<version>...HEAD`, and `[<version>]` comparing the previous tag with `<version>`. The file
  is created with the Keep a Changelog header if it is missing.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/publish-cord -p 'test_*.py'
```

The tests use fake `gh`, `curl` and stats-script executables and a local bare
repository as `origin`, so they need no network. `bin/test_get_cord_stats.py` tests the stats
script against a small workbook, and is skipped without `openpyxl`.
