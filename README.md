# Senzing CORDs

Senzing Collections of Relatable Data (CORDs) are free, Senzing-ready JSONL data sets, grouped by
locale, for evaluating entity resolution.

Browse and download them in the [CORD catalog](https://hub.dockter.com/senzing-cord-development-test/).

[Senzing-ready data collections (CORD)](https://senzing.com/senzing-ready-data-collections-cord/)
explains what they are and how to use them.

```bash
curl -fsSLO https://github.com/docktermj/senzing-cord-development-test/releases/download/0.1.1/gleif-london.jsonl
sha256sum gleif-london.jsonl   # compare with the SHA-256 in the catalog
```

## References

- [Download every CORD](development/bulk-download.md)
- [How to publish CORDs](development/publishing-cords.md)
- [Submit a CORD without write access](development/submit-without-write-access.md)
- [CORD publishing approach](development/publishing-approach.md)
