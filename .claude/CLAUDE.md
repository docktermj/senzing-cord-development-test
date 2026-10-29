# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A test copy of senzing-garage/cord-development, used to work out how to publish the Senzing
Collections of Relatable Data (CORD): 21 JSONL files, about 1.68 GB in all, served today from
`https://senzing.com/datasets/`. It has no application code, build, tests or CI.

## How the data is published

`docs/publishing-approach.md` records the decision and its reasoning. Read it before
changing anything about storage. In short:

- **The data files never go in git.** Two of them are over GitHub's 100 MiB limit, and
  `/senzing-ready` is gitignored as a local working directory for downloads.
- **Each file is a GitHub Release asset** of the Release where it last changed. That gives
  it a public `curl` URL, the entry's `url` in `manifest.json`:
  `https://github.com/docktermj/senzing-cord-development-test/releases/download/<tag>/<file>`.
- **Each file also has its own `ghcr.io` repository,**
  `ghcr.io/docktermj/senzing-cord-development-test/<name>`, pushed with ORAS under the tag
  `sha256-<hex>`. The layer digest equals the file's SHA-256. The `v0.1.0` proof of concept
  used a single artifact, `…/senzing-cord-development-test:v0.1.0`, which is kept as
  history.
- **`manifest.json` is the source of truth in git.** It has one entry per file: `name`,
  `size`, `sha256`, `tag`, `url`, `oci` (the per-file repository) and an optional `source`.
  `tag` and `url` are `null` until the file is released. A data change is a PR that edits
  it.
- **Versions are bare SemVer tags** (`0.2.0`), each a Release holding only the files new or
  changed in that version. `releases/latest/download/<file>` therefore works only for files
  changed in the newest Release. The proof of concept, `v0.1.0`, is the only tag with a leading `v`, and counts
  as `0.1.0`.

## Commands

`/upload-senzing-ready` (`.claude/skills/upload-senzing-ready/`) pushes new and changed
`senzing-ready/*.jsonl` files and updates `manifest.json` and the `[Unreleased]` section of `CHANGELOG.md`. It commits
nothing. `/release-cord` (`.claude/skills/release-cord/`) then publishes them:

- it asks for the SemVer bump;
- it builds a draft Release of the new and changed files, and a `release-<version>` PR that
  also rewrites the `## CORDs` lists in `README.md` and the files' rows in `CORD_stats.xlsx`
  (through `bin/get_cord_stats.py`);
- it asks before merging;
- it then tags `ghcr.io` and publishes the draft on the merge commit.

`/download-senzing-ready` (`.claude/skills/download-senzing-ready/`) fills `senzing-ready/` from the
manifest. All three stop if only an old `jsonl/` directory exists; move it with
`mv jsonl senzing-ready`.

- released files come from their Release URL, and unreleased ones from `ghcr.io`;
- each file's SHA-256 is checked;
- a locally modified file is kept unless named with `--force <name>`.

`release_cord.py` and `download_senzing_ready.py` import shared helpers from
`upload_senzing_ready.py`. Tests:

```bash
for s in upload-senzing-ready release-cord download-senzing-ready; do
  python3 -m unittest discover -s .claude/skills/$s -p 'test_*.py'
done
python3 -m unittest discover -s bin -p 'test_*.py'   # skipped without openpyxl
```

File names must be `<source>-<region>.jsonl`; `REGIONS` in `upload_senzing_ready.py` maps each
region to its README heading. `/upload-senzing-ready` refuses other names, and `/release-cord`
applies the same check. The only Python dependency, `openpyxl`, is in `pyproject.toml`
(`pip install .`).

The document's "Proof of concept" section has the full publish and download commands for
`v0.1.0`.

```bash
# Publish a file under a new tag. Run from the directory that holds it: ORAS rejects absolute paths.
gh release create <tag> <file> --title "CORD data <tag>" --notes "..."
gh auth token | oras login ghcr.io -u docktermj --password-stdin   # needs write:packages
oras push ghcr.io/docktermj/senzing-cord-development-test:<tag> \
  --artifact-type application/vnd.senzing.cord.v1 \
  --annotation org.opencontainers.image.source=https://github.com/docktermj/senzing-cord-development-test \
  <file>:application/x-ndjson

# Verify: each must print the manifest's sha256
curl -fsSL https://senzing.com/datasets/<file> | sha256sum
curl -fsSL <release-url> | sha256sum
oras blob fetch --output - ghcr.io/docktermj/senzing-cord-development-test@sha256:<sha256> | sha256sum

# List the ghcr.io tags of every file in the manifest
for oci in $(jq -r '.files[].oci' manifest.json); do
  echo "$oci: $(oras repo tags "$oci" | tr '\n' ' ')"
done

markdownlint README.md docs/*.md   # MD013 (line length) is known to fail; there is no lint config
```

## Constraints

- **The `ghcr.io` package is private.** Making it public is UI-only and can't be undone, so
  don't do it without an explicit decision.
- **Don't change upstream.** Don't change links on the CORD page
  (<https://senzing.com/senzing-ready-data-collections-cord/>), and don't remove anything from
  `https://senzing.com/datasets/`. Moving the other 19 files and updating those links is
  follow-up work.
- **Check SHA-256 on every publish.** A Release asset must match the copy on senzing.com, the
  `ghcr.io` blob and the manifest entry.
