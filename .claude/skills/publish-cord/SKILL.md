---
name: publish-cord
description: Publish a SemVer release of the CORD JSONL files — a GitHub Release holding the new and changed files in manifest.json, and the manifest.json, CHANGELOG.md, development/cords.json, GitHub Pages catalog (docs/index.html, docs/cords.html, docs/cords.md) and development/CORD_stats.xlsx/.csv updates merged into main. Use when the user runs /publish-cord; it runs the /upload-senzing-ready upload itself first. Asks for the SemVer bump and before merging.
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
     - "region … has no catalog heading" means adding the region to `REGIONS` in
       `upload_senzing_ready.py`; "expected `<source>-<region>.jsonl`" means renaming the file.
     - "needs openpyxl" means running `pip install openpyxl`, or `pip install .` from the
       repository root.
     - "no entry in development/cords.json" means adding the file's entry to
       `development/cords.json` (see `docs/cords.html` below). It may stay uncommitted;
       `prepare` commits it.
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
   - runs `bin/get_cord_stats.py` on those same files to update `development/CORD_stats.xlsx`
     and `development/CORD_stats.csv`, taking each
     from `senzing-ready/` when its SHA-256 matches and from the draft otherwise;
   - rewrites the GitHub Pages files `docs/index.html`, `docs/cords.html` and `docs/cords.md`;
   - commits `manifest.json`, `CHANGELOG.md`, `development/cords.json`, those three Pages files,
     `development/CORD_stats.xlsx` and `development/CORD_stats.csv` on the branch
     `release-<version>`;
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
- **`docs/index.html` and `docs/cords.html`:** the end-user landing page and CORD catalog that
  GitHub Pages serves from `main:/docs` (`PAGES_URL` in `publish_cord.py`). They are generated
  from `manifest.json`, `development/cords.json`, `REGIONS` in `upload_senzing_ready.py`,
  `development/CORD_stats.csv` and `CHANGELOG.md`, never from the data files. The catalog has
  one section per `REGIONS` heading, in alphabetical order, listing only released files (`tag`
  not `null`) by name. Each entry shows the synopsis, locale, primary source, license, snapshot
  date, version with its `CHANGELOG.md` date and link, size, record counts, SHA-256, a badge per
  non-zero `*_FEATURES` column, `DATA_SOURCE`, download link and `curl` and `sha256sum`
  commands, a collapsed sample record and the related CORDs in the same locale. A `null`
  `cords.json` value shows as "Not stated".
- **`docs/cords.md`:** a stub linking to `cords.html`. `docs/.nojekyll` keeps GitHub Pages from
  running Jekyll.
- **Rewriting the Pages files:** `python3 .claude/skills/publish-cord/publish_cord.py catalog`
  rewrites all three, offline (no network or `gh`), and leaves current files unchanged. Run it
  after any change to its inputs outside a release, such as a `cords.json` edit or
  `/upload-senzing-ready --remove`. It stops, writing nothing, on a region missing from
  `REGIONS`, a released file without a `cords.json` entry, a `CORD_stats.csv` row (matched on
  `SOURCE` and `GEO`) or a `## [<tag>] - <date>` heading in `CHANGELOG.md`, and on a
  `cords.json` entry without a `manifest.json` entry.
- **`development/cords.json`:** hand-edited, keyed by file name: `synopsis`, `primary_source`
  (`name` and `url`), `license`, `snapshot_date`, `data_source` and `sample_record`. A file
  can't be released without an entry.
- **`README.md`:** not changed; its `## CORDs` section links to the catalog.
- **`development/CORD_stats.xlsx`:** `bin/get_cord_stats.py` rewrites the row of each new and changed
  file, matched on `SOURCE` and `GEO`. It sets the counts (a feature no longer present becomes
  0), `LAST_UPDATED` (the release date) and `UPDATE_REASON` (`Added in <version>` or
  `Changed in <version>`, following `[Unreleased]`). A new file gets a new row. No other cell
  changes.
- **`development/CORD_stats.csv`:** rewritten from the workbook by the same run: the same rows
  and columns, dates as `YYYY-MM-DD`. A test fails while it is out of date; after a hand edit
  to the workbook, rewrite it with
  `python3 bin/get_cord_stats.py --stats development/CORD_stats.xlsx`.
- **`CHANGELOG.md`:** `## [Unreleased]` becomes `## [<version>] - YYYY-MM-DD` under a new
  empty `## [Unreleased]`. The link definitions get `[Unreleased]` comparing
  `<version>...HEAD`, and `[<version>]` comparing the previous tag with `<version>`. The file
  is created with the Keep a Changelog header if it is missing.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/publish-cord -p 'test_*.py'
```

The tests use fake `gh`, `curl` and stats-script executables and a local bare
repository as `origin`, so they need no network. They also fail when a committed
Pages file (`docs/index.html`, `docs/cords.html`, `docs/cords.md`) differs from what its inputs
give.
`bin/test_get_cord_stats.py` tests the stats script against a small workbook, and is skipped
without `openpyxl`.
