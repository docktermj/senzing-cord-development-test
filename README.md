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

Each link below serves the version of the file in `manifest.json`, from the Release where it
last changed. Files are listed per region, from the part of the file name after the last
hyphen. `/release-cord` rewrites these lists on every release.

### Las Vegas

- [opendata-lasvegas.jsonl](https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.2/opendata-lasvegas.jsonl)
- [opendata2-lasvegas.jsonl](https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.3/opendata2-lasvegas.jsonl)

### London

- [gleif-london.jsonl](https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.1/gleif-london.jsonl)
- [ofac-london.jsonl](https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.1/ofac-london.jsonl)

### Moscow

Not published yet.

## Download

```bash
curl -fsSLO https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.1/gleif-london.jsonl
sha256sum gleif-london.jsonl   # compare with the sha256 in manifest.json
```

In a clone, `/download-senzing-ready` in Claude Code downloads every file in `manifest.json` into
`senzing-ready/` and checks each SHA-256.

## References

- [JSONL file download approach](docs/publishing-approach.md): how this repository publishes the CORD JSONL files, and why.
