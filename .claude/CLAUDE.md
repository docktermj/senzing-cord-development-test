# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A test copy of senzing-garage/cord-development, used to work out how to publish the Senzing
Collections of Relatable Data (CORD): 21 JSONL files, about 1.68 GB in all, served today from
`https://senzing.com/datasets/`. It has no application code or build.

## How the data is published

`docs/publishing-approach.md` records the decision and its reasoning. Read it before
changing anything about storage. In short:

- **The data files never go in git.** Two of them are over GitHub's 100 MiB limit, and
  `/senzing-ready` is gitignored as a local working directory for downloads.
- **Each file is a GitHub Release asset** of the Release where it last changed. That gives
  it a public `curl` URL, the entry's `url` in `manifest.json`:
  `https://github.com/docktermj/senzing-cord-development-test/releases/download/<tag>/<file>`.
- **Unreleased files are staged on a draft Release tagged `unreleased`.** `/publish-cord`
  renames it to the version it releases, so the files are never uploaded twice. A draft is
  visible only to the repository's writers. Each asset's `digest` is checked against the
  manifest's `sha256`.
- **`ghcr.io` is no longer used** (issue #26). Its packages, from the `v0.1.0` proof of
  concept and the per-file repositories `ghcr.io/docktermj/senzing-cord-development-test/<name>`,
  are kept as history.
- **`manifest.json` is the source of truth in git.** It has one entry per file: `name`,
  `size`, `sha256`, `tag`, `url` and an optional `source`.
  `tag` and `url` are `null` until the file is released. A data change is a PR that edits
  it.
- **`source` is the file's senzing.com URL when that copy has the same SHA-256.**
  `/upload-senzing-ready` sets it for each added or changed file: it compares the `Content-Length`
  of `https://senzing.com/datasets/<name>` with the file's size, and downloads and hashes the copy
  only when they match. `source` is `null` after a 404 or a mismatch; any other failure
  stops the run before anything is uploaded. A repaired file keeps its `source`.
- **Versions are bare SemVer tags** (`0.2.0`), each a Release holding only the files new or
  changed in that version. `releases/latest/download/<file>` therefore works only for files
  changed in the newest Release. The proof of concept, `v0.1.0`, is the only tag with a leading `v`, and counts
  as `0.1.0`.

## Commands

`/upload-senzing-ready` (`.claude/skills/upload-senzing-ready/`) uploads new and changed
`senzing-ready/*.jsonl` files to the `unreleased` draft and updates `manifest.json` and the `[Unreleased]` section of `CHANGELOG.md`. It commits
nothing. `/publish-cord` (`.claude/skills/publish-cord/`) publishes them:

- it first runs the upload itself (`publish_cord.py upload`): a preview, then the upload, stopping
  on any refused file; it skips this while resuming a draft;
- it asks for the SemVer bump;
- it renames the `unreleased` draft to `<version>`, and opens a `release-<version>` PR that
  also rewrites the `## CORDs` lists in `README.md`, the catalog `docs/cords.md` and the files'
  rows in `CORD_stats.xlsx` (through `bin/get_cord_stats.py`);
- it asks before merging;
- it then publishes the draft on the merge commit.

`docs/cords.md`, the CORD catalog that GitHub Pages serves from `main:/docs`, is generated from
`manifest.json` and never edited by hand. After a manifest change outside a release, such as
`/upload-senzing-ready --remove`, rewrite it with `publish_cord.py catalog`, which needs no
network or `gh`. A test fails while it is out of date.

`/download-senzing-ready` (`.claude/skills/download-senzing-ready/`) fills `senzing-ready/` from the
manifest. All three stop if only an old `jsonl/` directory exists; move it with
`mv jsonl senzing-ready`.

- released files come from their Release URL, and unreleased ones from the `unreleased`
  draft (which needs `gh auth login`);
- each file's SHA-256 is checked;
- a locally modified file is kept unless named with `--force <name>`.

`download_senzing_ready.py --verify` writes nothing: it checks each released file's
Release asset `digest` (through `gh`, without downloading it) and, when it has a `source`,
the senzing.com copy against the manifest's `sha256`. `.github/workflows/check-releases.yml`
runs it weekly and on `workflow_dispatch`, and fails on any mismatch or missing file.

`publish_cord.py` and `download_senzing_ready.py` import shared helpers from
`upload_senzing_ready.py`. Tests:

```bash
for s in upload-senzing-ready publish-cord download-senzing-ready; do
  python3 -m unittest discover -s .claude/skills/$s -p 'test_*.py'
done
python3 -m unittest discover -s bin -p 'test_*.py'   # skipped without openpyxl
```

CI (`.github/workflows/test.yml`) runs these on every pull request and on pushes to `main`,
on Python 3.10 and 3.12, after `pip install .`, and fails if `openpyxl` is missing rather
than skipping the `bin` tests. A separate `markdownlint` job runs the lint command below with
`markdownlint-cli` 0.48.0, so any lint error fails CI.

File names must be `<source>-<region>.jsonl`; `REGIONS` in `upload_senzing_ready.py` maps each
region to its README heading. `/upload-senzing-ready` refuses other names, and `/publish-cord`
applies the same check. The only Python dependency, `openpyxl`, is in `pyproject.toml`
(`pip install .`).

```bash
# Verify: each must print the manifest's sha256
curl -fsSL https://senzing.com/datasets/<file> | sha256sum
curl -fsSL <release-url> | sha256sum

# The digest of every asset on the staging draft
gh release view unreleased --json assets --jq '.assets[] | "\(.name) \(.digest)"'

# Lint, as CI does (.markdownlint.json: MD013 and MD041 off, MD024 siblings_only;
# .markdownlintignore skips .claude/worktrees/). Quote the globs so markdownlint expands them.
markdownlint README.md CHANGELOG.md 'docs/*.md' '.claude/*.md' '.claude/**/*.md'
```

## Constraints

- **Don't change upstream.** Don't change links on the CORD page
  (<https://senzing.com/senzing-ready-data-collections-cord/>), and don't remove anything from
  `https://senzing.com/datasets/`. Moving the rest of the 21 files and updating those links
  is follow-up work.
- **Check SHA-256 on every publish.** A Release asset must match the copy on senzing.com and
  the manifest entry.
