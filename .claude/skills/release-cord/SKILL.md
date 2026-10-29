---
name: release-cord
description: Publish a SemVer release of the CORD JSONL files — a GitHub Release holding every file in manifest.json, ghcr.io version tags, and the manifest.json and CHANGELOG.md updates merged into main. Use when the user runs /release-cord, usually after /upload-senzing-ready. Asks for the SemVer bump and before merging.
disable-model-invocation: true
---

Publish the files in `manifest.json` as a new release. The script `release_cord.py` next
to this file does each step. This skill runs it and asks the user the two questions it can't
answer: the SemVer bump, and whether to merge.

## Rules

- Versions are bare SemVer (`0.2.0`), never `v0.2.0`. The old `v0.1.0` counts as `0.1.0`.
- If only an old `jsonl/` directory exists, the script stops with exit 2 and prints
  `mv jsonl senzing-ready`. Show that to the user; don't move it yourself.
- Never merge without the user's explicit yes in step 4, in this run.
- Never delete a draft Release, branch, tag or package, and never change package visibility.
  When a step stops, leave things as they are; a rerun resumes from the draft.

## Steps

1. Run, from the repository root:

   ```bash
   python3 .claude/skills/release-cord/release_cord.py status
   ```

   - **Exit 2:** show each `blocking:` line and stop. "Nothing to release" means running
     `/upload-senzing-ready` first. A missing `sha256` tag means rerunning `/upload-senzing-ready` with the
     file in `senzing-ready/`.
   - **`draft: <version> (resume)`:** say so, skip the bump question, and go to the step that
     `next:` names (`prepare` is step 3, `publish` is step 4).

2. Ask the user which part of the version to bump: Major, Minor or Patch. Show the version
   each choice gives (the `major:`, `minor:` and `patch:` lines) and recommend the
   `suggested bump:`. It is Major if `[Unreleased]` has Removed lines, Minor if it has Added
   lines, and Patch otherwise. Wait for the answer.

3. Run, with the chosen bump:

   ```bash
   python3 .claude/skills/release-cord/release_cord.py prepare --bump <major|minor|patch>
   ```

   This step:
   - creates the draft Release `<version>` holding every manifest file, taking each from
     `senzing-ready/` when its SHA-256 matches and from `ghcr.io` otherwise;
   - commits `manifest.json` and `CHANGELOG.md` on the branch `release-<version>`;
   - pushes the branch and opens the PR.

   Report the `pull request:` URL. Exit 2 means it stopped; show the message. A rerun (go back
   to step 1) resumes the draft.

4. Show the PR and ask: "Merge `<PR URL>` with `--merge --delete-branch`, then tag
   `ghcr.io` and publish `<version>`?" Wait for an explicit yes.
   - **No:** say that the draft Release and the branch are left in place, that no
     `ghcr.io` tag was added, and that `/release-cord` resumes later. Stop.
   - **Yes:** run:

     ```bash
     python3 .claude/skills/release-cord/release_cord.py publish
     ```

     This step merges the PR, tags every file's `ghcr.io` repository with `<version>`,
     publishes the draft on the merge commit as the latest Release, switches the clone to an
     updated `main`, and downloads every Release URL to check its SHA-256.

5. Report the merge commit, the `tagged:` and `published:` lines, and each `verified:` line.
   Exit 1 means a download didn't match `manifest.json`: list the `MISMATCH:` lines and
   leave the Release as it is for the user to look at. Exit 2 means it stopped before
   publishing, for example because branch protection refused the merge. Show the message;
   the draft is still unpublished.

## What a release changes

- **Release:** `<version>`, holding every file. Its notes are exactly
  `See [CHANGELOG.md](https://github.com/<owner>/<repo>/blob/main/CHANGELOG.md#<anchor>)`.
  `<anchor>` is GitHub's anchor for `[<version>] - YYYY-MM-DD`: brackets and dots dropped,
  spaces turned to hyphens, so `[0.2.0] - 2026-10-28` gives `020---2026-10-28`.
- **`manifest.json`:** every entry gets `tag` `<version>` and
  `url` `https://github.com/<owner>/<repo>/releases/download/<version>/<name>`.
- **`CHANGELOG.md`:** `## [Unreleased]` becomes `## [<version>] - YYYY-MM-DD` under a new
  empty `## [Unreleased]`. The link definitions get `[Unreleased]` comparing
  `<version>...HEAD`, and `[<version>]` comparing the previous tag with `<version>`. The file
  is created with the Keep a Changelog header if it is missing.
- **`ghcr.io`:** `oras tag <oci>:sha256-<hex> <version>` for every entry, after the merge.

## Tests

```bash
python3 -m unittest discover -s .claude/skills/release-cord -p 'test_*.py'
```

The tests use fake `gh`, `oras` and `curl` executables and a local bare repository as
`origin`, so they need no network.
