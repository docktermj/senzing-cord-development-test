# senzing-cord-development-test

A test copy of [senzing-garage/cord-development](https://github.com/senzing-garage/cord-development),
used to work out how to publish the Senzing Collections of Relatable Data (CORD).

The data files are not stored in git. Each version is a
[GitHub Release](https://github.com/docktermj/senzing-cord-development-test/releases) holding
the files that are new or changed in that version, and [`manifest.json`](manifest.json) lists
each file's size, SHA-256 and download URL: the Release where it last changed.
[`CHANGELOG.md`](CHANGELOG.md) says what changed in each version, and
[`development/CORD_stats.xlsx`](development/CORD_stats.xlsx) holds record and feature counts for
each file, also written as [`development/CORD_stats.csv`](development/CORD_stats.csv).

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

## References

- [How to publish CORDs](development/publishing-cords.md): the steps for a writer to this repository.
- [Submit a CORD without write access](development/submit-without-write-access.md): how anyone with a GitHub account hands over a new or changed file.
- [CORD publishing approach](development/publishing-approach.md): how this repository publishes the CORD JSONL files, and why.
