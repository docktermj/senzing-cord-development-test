# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A test copy of senzing-garage/cord-development, used to work out how to publish the Senzing
Collections of Relatable Data (CORD): 21 JSONL files, about 1.68 GB in all, served today from
`https://senzing.com/datasets/`. It has no application code, build, tests or CI.

## How the data is published

`docs/jsonl-download-approach.md` records the decision and its reasoning. Read it before
changing anything about storage. In short:

- **The data files never go in git.** Two of them are over GitHub's 100 MiB limit, and
  `/jsonl` is gitignored as a local working directory for downloads.
- **Each file is a GitHub Release asset.** That gives it a public `curl` URL:
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
- **Versions are semver tags.** The proof of concept is `v0.1.0`, which holds only
  `gleif-london.jsonl`.

## Commands

`/upload-jsonl` (`.claude/skills/upload-jsonl/`) pushes new and changed `jsonl/*.jsonl` files
and updates `manifest.json` and the `[Unreleased]` section of `CHANGELOG.md`. It commits
nothing. Its tests:

```bash
python3 -m unittest discover -s .claude/skills/upload-jsonl -p 'test_*.py'
```

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
