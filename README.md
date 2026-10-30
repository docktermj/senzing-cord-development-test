# senzing-cord-development-test

A test copy of [senzing-garage/cord-development](https://github.com/senzing-garage/cord-development),
used to work out how to publish the Senzing Collections of Relatable Data (CORD).

The data files are not stored in git. Each version is a
[GitHub Release](https://github.com/docktermj/senzing-cord-development-test/releases) holding
the files that are new or changed in that version, and [`manifest.json`](manifest.json) lists
each file's size, SHA-256 and download URL: the Release where it last changed.
[`CHANGELOG.md`](CHANGELOG.md) says what changed in each version, and
[`CORD_stats.xlsx`](CORD_stats.xlsx) holds record and feature counts for each file.

## CORDs

The [CORD catalog](docs/cords.md) lists every released file with its version, size, SHA-256
and download link, one table per region. `/publish-cord` rewrites it on every release.

## Download

```bash
curl -fsSLO https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.1/gleif-london.jsonl
sha256sum gleif-london.jsonl   # compare with the sha256 in manifest.json
```

In a clone, `/download-senzing-ready` in Claude Code downloads every file in `manifest.json` into
`senzing-ready/` and checks each SHA-256.

## How to publish CORDs

You need write access to this repository. Without it, see
[Submit a CORD without write access](#submit-a-cord-without-write-access).

1. Clone the repository, then run `gh auth login` and `pip install .`.
2. Optional: in Claude Code, run `/download-senzing-ready` to fill `senzing-ready/` with the
   current files. Uploads look only at the files in `senzing-ready/`, so you need just the
   ones you add or replace.
3. Add a new file to `senzing-ready/`, or replace an existing one. Name it
   `<source>-<region>.jsonl`, such as `gleif-london.jsonl`.
   - A new region first needs an entry in `REGIONS` in
     `.claude/skills/upload-senzing-ready/upload_senzing_ready.py`.
   - To remove a file, run `/upload-senzing-ready --remove <name>` instead.
4. Run `/publish-cord`. It uploads the new and changed files, asks which part of the version to
   bump, and opens a `release-<version>` pull request. Approve the merge when it asks, and it
   publishes the Release.

[CORD publishing approach](docs/publishing-approach.md#current-workflow) explains each step
and why the files are published this way.

## Submit a CORD without write access

Anyone with a GitHub account can hand over a new or changed file. A writer checks it and
publishes it.

1. Name the file `<source>-<region>.jsonl`, in lowercase, such as `gleif-london.jsonl`. The
   region, after the last hyphen, must be one of the headings under [CORDs](#cords), written
   without spaces, such as `lasvegas`; ask in an issue before using a new one. To replace a
   published file, keep its name.
2. Host the file at a public HTTPS link that `curl -fsSL` downloads without authentication. A
   Release asset on your fork of this repository works, and takes files up to 2 GiB:
   1. On this repository's page, click **Fork**, then **Create fork**.
   2. On your fork, open **Releases** and click **Draft a new release**, or go to
      `https://github.com/<you>/senzing-cord-development-test/releases/new`.
   3. Under **Choose a tag**, type a new tag, such as `submit-gleif-london`, and click
      **Create new tag**.
   4. Give the release a title, and drag the file onto the **Attach binaries** box. Wait for the
      upload to finish.
   5. Click **Publish release**. The assets of a draft aren't public.
   6. Under **Assets**, right-click the file name and copy the link. It looks like
      `https://github.com/<you>/senzing-cord-development-test/releases/download/<tag>/<file>`.
3. Get the file's size in bytes and its SHA-256:

   ```bash
   stat -c %s gleif-london.jsonl   # macOS: stat -f %z gleif-london.jsonl
   sha256sum gleif-london.jsonl    # macOS: shasum -a 256 gleif-london.jsonl
   ```

   `curl -fsSL <link> | sha256sum` must print the same SHA-256.
4. Open a
   [Submit a CORD file](https://github.com/docktermj/senzing-cord-development-test/issues/new?template=submit-cord.yml)
   issue, and fill in the file name, link, size and SHA-256.

The writer downloads the file, checks its size and SHA-256, and publishes it with
`/publish-cord`. If the download fails, a value doesn't match, or the file fails the upload
checks, the writer says why in the issue; post a corrected link or values there. Once the issue
is closed with a link to the Release, you can delete your hosted copy.

## References

- [CORD publishing approach](docs/publishing-approach.md): how this repository publishes the CORD JSONL files, and why.
