# Download every CORD

Each CORD file is a GitHub Release asset of the Release where it last changed, so there is no
single URL for all of them. The CORD catalog on GitHub Pages publishes two lists of the
released files instead, both generated from `manifest.json` by `publish_cord.py catalog`:

- [`urls.txt`](https://hub.dockter.com/senzing-cord-development-test/urls.txt): one download
  URL per line;
- [`SHA256SUMS`](https://hub.dockter.com/senzing-cord-development-test/SHA256SUMS): one
  `<sha256>  <name>` line per file, in the format `sha256sum -c` reads.

Files that are staged but not yet released are not listed.

## Download and verify

Run this in an empty directory, or in one that already holds some of the files. It needs
`curl` and GNU `sha256sum`, and downloads about 1.7 GB.

```bash
base=https://hub.dockter.com/senzing-cord-development-test
curl -fsSLO "$base/SHA256SUMS" && curl -fsSLO "$base/urls.txt" || exit 1
while read -r url; do
  name=${url##*/}
  # Keep a file whose SHA-256 already matches; download anything else.
  grep -F "  $name" SHA256SUMS | sha256sum -c --status 2>/dev/null \
    || curl -fsSLO "$url" || { echo "download failed: $url" >&2; exit 1; }
done < urls.txt
sha256sum -c SHA256SUMS
```

- **A rerun** downloads only the files that are missing or don't match, so it also resumes an
  interrupted run.
- **A failed download** stops the loop with a non-zero exit.
- **The last line** checks every file, prints `<name>: OK` for each, and exits non-zero on any
  mismatch.

On macOS, use `shasum -a 256` in place of `sha256sum`.

## Where the lists come from

`docs/SHA256SUMS` and `docs/urls.txt` are written by
`python3 .claude/skills/publish-cord/publish_cord.py catalog`, with the rest of the catalog,
and are never edited by hand. `/publish-cord` regenerates them in every release PR, and a test
fails while they are out of date with `manifest.json`.
