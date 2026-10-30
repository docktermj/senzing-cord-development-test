# CORD publishing approach

Decision record for [issue #1](https://github.com/docktermj/senzing-cord-development-test/issues/1),
copied from senzing-garage/cord-development#4.

## Decision

Publish each CORD JSONL file as a **GitHub Release asset, recorded in a manifest in git**.
This gives every file a stable public HTTPS URL that downloads with `curl` and no
authentication.

Before it is released, a file is staged on a **draft Release tagged `unreleased`**.
`/publish-cord` renames that draft to the version it releases, so a file is uploaded once.
Each asset's `digest` is checked against the manifest's SHA-256.

`manifest.json` at the repository root holds one entry per file, giving its name, size in
bytes, SHA-256, Release tag, download URL and, optionally, its senzing.com `source`. A data
change is a pull request that edits the manifest.

The reasons are in [Why this approach](#why-this-approach). Each file was at first also
published to `ghcr.io` (approach 4); [Dropping `ghcr.io`](#dropping-ghcrio) says why that
stopped.

## Current workflow

A writer to this repository, in Claude Code:

1. **`/download-senzing-ready`** fills the gitignored `senzing-ready/` directory from
   `manifest.json`, checking each file's SHA-256.
2. Add or edit `senzing-ready/<source>-<region>.jsonl`.
3. **`/upload-senzing-ready`** uploads the new and changed files to the `unreleased` draft
   Release, and records them in `manifest.json` (with `tag` and `url` still `null`) and the
   `[Unreleased]` section of `CHANGELOG.md`. It refuses a file whose region isn't in
   `REGIONS`, and `--remove <name>` takes a file out of the manifest.
4. **`/publish-cord`** runs that upload itself, asks for the SemVer bump, renames the draft to
   the new version, and opens a `release-<version>` pull request that updates `manifest.json`,
   `CHANGELOG.md`, the catalog `docs/cords.md` (which README links to) and
   `CORD_stats.xlsx`. After the user approves the merge, it publishes the Release on the merge
   commit.
5. **`check-releases.yml`** runs `download_senzing_ready.py --verify` weekly. It fails if a
   released file's asset `digest`, or its senzing.com copy, no longer matches the manifest's
   `sha256`.

### A submission from a contributor without write access

A contributor without write access opens a **Submit a CORD file** issue, from
`.github/ISSUE_TEMPLATE/submit-cord.yml`. It gives the file name, a public download URL, the
size in bytes and the SHA-256. The README's
[Submit a CORD without write access](../README.md#submit-a-cord-without-write-access) has the
contributor's steps. A writer, in a clone:

1. **Downloads** the file: `curl -fsSL -o senzing-ready/<name> <url>`.
2. **Checks** that `stat -c %s senzing-ready/<name>` and `sha256sum senzing-ready/<name>` print
   the size and SHA-256 in the issue.
3. **Runs `/publish-cord`**, whose upload step applies the upload checks: a
   `<source>-<region>.jsonl` name, a region in `REGIONS`, at most 2 GiB, and every line valid
   JSON.
4. **Closes the issue** with a link to the Release.

If the download fails, the size or SHA-256 doesn't match, or the upload refuses the file, the
writer publishes nothing and says why in a comment on the issue. The contributor then posts a
corrected link or values. `/publish-cord` stops on any refused file, and a new region needs a
writer to add it to `REGIONS` first. A replacement for an existing file is handled the same way
as a new one: the upload treats a changed SHA-256 as a change.

The contributor's URL is not recorded as the manifest's `source`. `check-releases.yml`
re-downloads every `source` weekly, and would fail once the contributor removed their copy. The
URL stays only in the issue. The published Release asset is the only copy that matters, so the
contributor can delete theirs once the issue is closed.

## Context

The [CORD page] links 21 JSONL files, all served from `https://senzing.com/datasets/`. The
sizes, checked on 2026-10-27 with `curl -I`, total 1,684,130,512 bytes (1.68 GB); see
[Appendix: the 21 files](#appendix-the-21-files). Two of them are over GitHub's 100 MiB
per-file limit for git:

- `opendata-lasvegas.jsonl` is 980,688,237 bytes.
- `equifax-lasvegas.jsonl` is 476,799,997 bytes.

`npi-lasvegas.jsonl` (67,818,080 bytes) is over the 50 MiB warning threshold.

This document compares the approaches for senzing-garage/cord-development, where GitHub Pages
already serves `main:/docs`. This repository is a test copy of it, and the proof of concept is
published here.

## The five approaches

| # | Approach | Where the bytes live |
| --- | --- | --- |
| 1 | GitHub Pages from `/docs` | Committed under `docs/` and served by Pages |
| 2 | A normal folder in git | Committed in a folder and served by `raw.githubusercontent.com` |
| 3 | Manifest in git, files as Release assets | Attached to a GitHub Release |
| 4 | Manifest in git, files in `ghcr.io` as OCI artifacts | Blobs in the Container registry |
| 5 | Shards in git, rejoined by a tag workflow into Release assets | Shards committed in git, plus the rejoined files as Release assets |

## Comparison

| Criterion | 1 Pages | 2 Folder | 3 Release | 4 `ghcr.io` | 5 Shards |
| --- | --- | --- | --- | --- | --- |
| Every one of the 21 files gets a stable public HTTPS URL that downloads with `curl` and no authentication | No | No | **Yes** | No | **Yes** |
| Holds all 21 files | No | No | **Yes** | **Yes** | **Yes** |
| Has to split files | Cannot help | Cannot help | **No** | **No** | Yes, 2 files |
| A data change shows in a PR as a diff of the records that changed | Partly | Partly | No | No | **Yes** |
| Data sits in the tree that `actions/checkout` fetches | Yes | Yes | **No** | **No** | Yes |
| Updating a file adds a full copy to the history `git clone` downloads | Yes | Yes | **No** | **No** | Yes |

The sections below give the reasoning behind each cell, and how a contributor adds or updates a
file larger than 100 MiB.

### 1. GitHub Pages from `/docs`

- **URL:** a committed file is served at `https://garage.senzing.com/cord-development/<path>`,
  which is public, works with `curl` and needs no authentication. The two large files can't be
  committed, so they get no URL.
- **Capacity:** no. GitHub blocks a push of any file over 100 MiB. "Git LFS cannot be used with
  GitHub Pages sites", so LFS can't get around that. Splitting would get the files past git,
  but Pages would then serve shards rather than the files. The total of 1.68 GB is also over
  the 1 GB limit for a published Pages site.
- **PR diff:** partly. Git computes a record-level diff, but GitHub's pull request view doesn't
  render large diffs by default, so a reviewer has to load them file by file.
- **Checkout:** every workflow's `actions/checkout` fetches the data, unless it uses
  `sparse-checkout` to leave `docs/` out. Here that would also drop the reference documents
  stored next to the data in `docs/`.
- **History:** each update stores a new blob of the whole file, and `git clone` downloads all
  of them. Pack delta compression can shrink small edits, but git skips it for files over
  `core.bigFileThreshold` (512 MiB by default).
- **Files over 100 MiB:** a contributor can't add one at all.

### 2. A normal folder in git

The same as approach 1, except that the URL is
`https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>`, and Pages' 1 GB site limit
doesn't apply. The 100 MiB block still rules out the two large files, and the repository would
pass GitHub's recommendation that repositories stay "ideally less than 1 GB".

### 3. Manifest in git, files as Release assets

- **URL:** yes.
  `https://github.com/<owner>/<repo>/releases/download/<tag>/<file>` redirects to GitHub's asset
  host, so `curl -fsSL` (which follows redirects) downloads it with no authentication. The URL
  stays stable as long as the Release and its tag are kept. Each Release holds only the files
  new or changed in it, so `releases/latest/download/<file>` works only for files changed in
  the newest Release; the manifest's `url` is the link that always works.
- **Capacity:** yes, without splitting. "Each file included in a release must be under 2 GiB",
  and `opendata-lasvegas.jsonl` is about half that. A Release can hold up to 1000 assets, and
  "there is no limit on the total size of a release, nor bandwidth usage."
- **PR diff:** no. The pull request shows the manifest's new size and SHA-256, not the records.
- **Checkout:** no. Only the manifest is in the tree.
- **History:** no. Git stores only manifest edits.
- **Files over 100 MiB:** no different from any other file. A writer puts it in
  `senzing-ready/` and runs `/upload-senzing-ready` and then `/publish-cord`, as in
  [Current workflow](#current-workflow); the pull request edits only the manifest. A
  contributor without write access hosts the file and opens an issue, as in
  [A submission from a contributor without write access](#a-submission-from-a-contributor-without-write-access).

### 4. Manifest in git, files in `ghcr.io` as OCI artifacts

- **URL:** no. A blob is fetched from
  `https://ghcr.io/v2/<owner>/<name>/blobs/sha256:<sha256>`, but only with a bearer token,
  which first has to be requested from `https://ghcr.io/token`. That's two requests, not one
  URL. The token request is anonymous only for a public package. Checked on 2026-10-27, it
  returns:
  - HTTP 403 `{"errors":[{"code":"DENIED","message":"requested access to the resource is denied"}]}`
    for a package that doesn't exist;
  - HTTP 401 `{"errors":[{"code":"UNAUTHORIZED","message":"authentication required"}]}`
    for a package that exists but is private, such as this proof of concept's.
- **Capacity:** yes, without splitting. "The Container registry has a 10 GB size limit for each
  layer", and "a 10 minute timeout limit for uploads". Pushing `opendata-lasvegas.jsonl` within
  that timeout takes about 1.6 MB/s of sustained upload.
- **Content addressing:** [ORAS] stores a file unchanged as a layer whose digest is the file's
  SHA-256. A file that is the same in two versions is stored once, and the manifest's `sha256`
  is the blob address.
- **PR diff, checkout, history:** the same as approach 3.
- **Files over 100 MiB:** the same as approach 3, except that the maintainer runs `oras push`
  with a token that has the `write:packages` scope.
- **Visibility:** "When you first publish a package, the default visibility is private", and a
  package linked to a repository inherits that repository's access permissions "but not the
  visibility". GitHub documents changing visibility only in the package settings UI.
  "Once you make a package public, you cannot make it private again."

### 5. Shards in git, rejoined by a tag workflow into Release assets

- **URL:** yes, as in approach 3. The workflow attaches each rejoined file to the Release.
- **Capacity:** yes, but only by splitting the two files over 100 MiB. `split -C 45M` breaks a
  file only at line ends. By the issue's measurement, it turns `opendata-lasvegas.jsonl` into
  21 shards of 37.0 to 47.2 MB, each valid JSONL on its own, and `cat` of the shards in order
  has the same SHA-256 as the original. 45M means 45 MiB, so each shard is under both the
  100 MiB block and the 50 MiB warning. The repository holds the full 1.68 GB, which is past
  the recommended 1 GB.
- **PR diff:** yes. Each shard is JSONL, so the pull request shows the changed records, subject
  to the large-diff rendering limit from approach 1.
- **Checkout:** yes. Every workflow fetches 1.68 GB unless it uses `sparse-checkout`.
- **History:** yes. Shards cut by size shift when records are added or removed, so an insertion
  near the start of `opendata-lasvegas.jsonl` changes every later shard. That is about 1 GB of
  new objects for a small edit.
- **Files over 100 MiB:** the contributor splits the file with `split -C 45M` and commits the
  shards in a pull request.

## Limits and edge cases

| Limit | 1 Pages | 2 Folder | 3 Release | 4 `ghcr.io` | 5 Shards |
| --- | --- | --- | --- | --- | --- |
| Git blocks a pushed file over 100 MiB and warns over 50 MiB | Blocks 2 files; `npi-lasvegas.jsonl` warns | Blocks 2 files; `npi-lasvegas.jsonl` warns | Not in git | Not in git | Shards stay under 45 MiB; `npi-lasvegas.jsonl` must be split too, or it warns |
| Repository recommended under 1 GB (all files 1.68 GB) | Exceeded | Exceeded | Manifest only | Manifest only | Exceeded |
| Pages site limited to 1 GB | Exceeded | Not used | Not used | Not used | Not used |
| Pages does not serve Git LFS files | Rules out LFS | Not used | Not used | Not used | Not used |
| Release asset limited to 2 GiB | Not used | Not used | Largest file is 0.98 GB | Not used | Largest rejoined file is 0.98 GB |
| `ghcr.io` layer limited to 10 GB; uploads time out after 10 minutes | Not used | Not used | Not used | Largest file is 0.98 GB | Not used |
| Anonymous `ghcr.io` pull needs a token; 403 `DENIED` if the package doesn't exist, 401 `UNAUTHORIZED` if it is private | Not used | Not used | Not used | Must be public for anonymous automation | Not used |
| New package is private; public cannot become private again | Not used | Not used | Not used | Starts private; going public is a one-way decision | Not used |
| Every revision adds a full copy to history | Yes | Yes | No | No | Yes |
| Size-cut shards shift on insertion | No shards | No shards | No shards | No shards | About 1 GB per small early edit |
| `actions/checkout` fetches committed data | Yes, unless sparse | Yes, unless sparse | No | No | Yes, unless sparse |
| Pages already serves `main:/docs` (legacy Jekyll build) | Data would share `docs/` with the reference documents | Unaffected | Unaffected | Unaffected | Unaffected |

## Why this approach

- **Only approaches 3 and 5 meet the hard requirement:** a stable public `curl` URL for all 21
  files. Approaches 1 and 2 can't hold the two large files. Approach 4 needs a token request
  before every download.
- **Approach 3 meets it without the costs of approach 5.** It needs no splitting, adds nothing
  to the history `git clone` downloads, and keeps the data out of `actions/checkout`. Approach 5
  buys record-level pull request diffs, but at 1.68 GB in the tree, a repository over GitHub's
  recommended size, and about 1 GB of history for a small edit to the largest file.
- **The cost is that pull requests show manifest changes, not record changes.** Reviewing the
  records themselves is left to the data-quality gate tracked by
  senzing-garage/cord-development#1.

### Dropping `ghcr.io`

Until [issue #26](https://github.com/docktermj/senzing-cord-development-test/issues/26), each
file was also pushed to its own `ghcr.io` repository as approach 4. That was meant to add what
automation needs. By then, Releases gave each of those things, or the copy no longer gave it:

- **Content addressing.** `/download-senzing-ready` checks every file's SHA-256 against the
  manifest, so a replaced asset is detected. Release assets also report a `digest`.
- **Storing unchanged files once.** Each Release holds only the files new or changed in it.
- **One command per version.** With a repository per file, `oras pull` fetched one file, the
  same as `curl`.
- **A copy for automation.** The package was private, so it needed a token. Release URLs
  need none.

Its one remaining job was holding uploaded files until they were released. The `unreleased`
draft Release does that. Dropping `ghcr.io` removed a second upload of every file, the ORAS
and `write:packages` requirements, and the open question of making the package public. No
consumer depended on it. The existing packages are kept as history, and the manifest no
longer has an `oci` field.

## Proof of concept (history)

The `oras` and `ghcr.io` steps below no longer apply; see [Dropping `ghcr.io`](#dropping-ghcrio)
and [Current workflow](#current-workflow).

`gleif-london.jsonl` (45,162,274 bytes, under the 100 MiB limit) is published in this repository
under the version tag `v0.1.0`. This records how the proof of concept was published, including
the `ghcr.io` copy that was later dropped.

| | Value |
| --- | --- |
| Source | `https://senzing.com/datasets/gleif-london.jsonl` |
| SHA-256 | `46d2f256f5c4c3c0aa5f7d0b01b90b9eb139bd4b97251e6d1ebf6ad99890cf55` |
| Release asset URL | <https://github.com/docktermj/senzing-cord-development-test/releases/download/v0.1.0/gleif-london.jsonl> |
| OCI artifact | `ghcr.io/docktermj/senzing-cord-development-test:v0.1.0` |
| Manifest entry | [`manifest.json`](../manifest.json), which now points at `0.1.1`: the same file, re-released |

### Publish

From a directory that holds the file (ORAS rejects absolute paths):

```bash
gh release create v0.1.0 gleif-london.jsonl \
  --repo docktermj/senzing-cord-development-test \
  --title "CORD data v0.1.0" \
  --notes "Proof of concept for #1: gleif-london.jsonl"

gh auth token | oras login ghcr.io -u docktermj --password-stdin   # token needs write:packages
oras push ghcr.io/docktermj/senzing-cord-development-test:v0.1.0 \
  --artifact-type application/vnd.senzing.cord.v1 \
  --annotation org.opencontainers.image.source=https://github.com/docktermj/senzing-cord-development-test \
  gleif-london.jsonl:application/x-ndjson
```

The `org.opencontainers.image.source` annotation links the package to this repository.

### Download

Anyone, with no authentication:

```bash
curl -fsSL -O https://github.com/docktermj/senzing-cord-development-test/releases/download/v0.1.0/gleif-london.jsonl
```

Automation, after `oras login ghcr.io` while the package is private:

```bash
oras pull ghcr.io/docktermj/senzing-cord-development-test:v0.1.0
# or one file, by the SHA-256 recorded in manifest.json:
oras blob fetch --output gleif-london.jsonl \
  ghcr.io/docktermj/senzing-cord-development-test@sha256:46d2f256f5c4c3c0aa5f7d0b01b90b9eb139bd4b97251e6d1ebf6ad99890cf55
```

### Verification

Each command must print the SHA-256 recorded in `manifest.json`:

```bash
curl -fsSL https://senzing.com/datasets/gleif-london.jsonl | sha256sum
curl -fsSL https://github.com/docktermj/senzing-cord-development-test/releases/download/v0.1.0/gleif-london.jsonl | sha256sum
oras blob fetch --output - ghcr.io/docktermj/senzing-cord-development-test@sha256:46d2f256f5c4c3c0aa5f7d0b01b90b9eb139bd4b97251e6d1ebf6ad99890cf55 | sha256sum
```

| Copy | SHA-256 matches | Checked |
| --- | --- | --- |
| `https://senzing.com/datasets/gleif-london.jsonl` | Yes | 2026-10-27 |
| Release asset `v0.1.0` | Yes | 2026-10-27 |
| `ghcr.io` blob | Yes | 2026-10-27 |

The `ghcr.io` package `senzing-cord-development-test` is linked to this repository, private,
and kept as history.

## Later improvements

- Turn on GitHub's immutable releases, so that once a Release is published its tag can't be
  moved and its assets can't be modified or deleted. That makes every Release asset URL in the
  manifest permanent.
- If shards in git are ever revisited, assign records to shards by a hash of `RECORD_ID`
  instead of by position. An insertion would then change one shard rather than every later
  one.

A follow-up issue covers the rest:

- [#42](https://github.com/docktermj/senzing-cord-development-test/issues/42): moving the rest of
  the 21 files and changing the [CORD page] links.

## Appendix: the 21 files

Sizes from `curl -I https://senzing.com/datasets/<file>` on 2026-10-27.

| File | Bytes |
| --- | ---: |
| `ofac-london.jsonl` | 102,967 |
| `icij-lasvegas.jsonl` | 164,513 |
| `open_sanctions-london.jsonl` | 257,591 |
| `us_labor_violations-lasvegas.jsonl` | 1,053,930 |
| `gleif-moscow.jsonl` | 1,169,270 |
| `gleif-lasvegas.jsonl` | 1,432,781 |
| `open_ownership-lasvegas.jsonl` | 1,518,843 |
| `ofac-moscow.jsonl` | 1,643,552 |
| `ppp_loans_over_150k-lasvegas.jsonl` | 2,103,822 |
| `open_ownership-moscow.jsonl` | 3,149,530 |
| `nominodata_risk-moscow.jsonl` | 3,626,050 |
| `open_sanctions-moscow.jsonl` | 8,583,131 |
| `nominodata_combined-lasvegas.jsonl` | 10,907,715 |
| `icij-moscow.jsonl` | 12,677,071 |
| `profound-lasvegas.jsonl` | 18,495,735 |
| `icij-london.jsonl` | 19,183,306 |
| `globaldata-london_central_a.jsonl` | 27,592,117 |
| `gleif-london.jsonl` | 45,162,274 |
| `npi-lasvegas.jsonl` | 67,818,080 |
| `equifax-lasvegas.jsonl` | 476,799,997 |
| `opendata-lasvegas.jsonl` | 980,688,237 |
| **Total** | **1,684,130,512** |

[CORD page]: https://senzing.com/senzing-ready-data-collections-cord/
[ORAS]: https://oras.land/
