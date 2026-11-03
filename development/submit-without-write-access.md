# Submit a CORD without write access

Anyone with a GitHub account can hand over a new or changed file. A writer checks it and
publishes it.

1. Name the file `<source>-<region>.jsonl`, in lowercase, such as `gleif-london.jsonl`. The
   region, after the last hyphen, must be one of the region headings in the [CORD catalog](https://hub.dockter.com/senzing-cord-development-test/cords.html), written
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
