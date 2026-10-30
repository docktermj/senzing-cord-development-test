#!/usr/bin/env python3
"""Download every file in manifest.json into senzing-ready/, checking each SHA-256.

Released entries come from their public Release URL; unreleased entries (url
null) come from the "unreleased" staging draft Release with gh. A current file
is skipped, and a file whose SHA-256 differs is kept unless named with --force.

With --verify it writes nothing: each released entry's Release asset digest is
compared with its sha256 through the GitHub API, and an entry with a source is
downloaded from it to a temporary directory, hashed and compared.

Exit codes: 0 senzing-ready/ matches the manifest (or, with --verify, every
released entry checks out), 1 some entries failed or are locally modified, 2
stopped before downloading anything.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "upload-senzing-ready")
)
import upload_senzing_ready as upload  # noqa: E402
from upload_senzing_ready import StopError  # noqa: E402


def fetch(root, entry, target):
    """Download entry to target; return an error message, or None."""
    if entry.get("url"):
        command = ["curl", "-fsSL", "-o", target, entry["url"]]
    else:
        command = ["gh", "release", "download", upload.STAGING, "--pattern", entry["name"],
                   "--output", target, "--clobber"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, cwd=root, check=False)
    except FileNotFoundError:
        return f"{command[0]} is not on PATH"
    if result.returncode == 0:
        return None
    error = result.stderr.strip() or f"{command[0]} exited with {result.returncode}"
    if command[0] == "gh" and re.search(r"40[134]|auth|credentials|not found", error, re.I):
        error += ("\nThe staging draft is visible only to the repository's writers; "
                  "log in with: gh auth login")
    return error


def run(root, force, out=None):
    out = out or sys.stdout
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.exists(manifest_path):
        raise StopError("manifest.json not found")
    entries = upload.load_manifest(manifest_path)["files"]
    unknown = sorted(set(force) - {entry["name"] for entry in entries})
    if unknown:
        raise StopError(f"not in manifest.json, cannot --force: {', '.join(unknown)}")

    ready_dir = upload.working_dir(root)
    os.makedirs(ready_dir, exist_ok=True)
    results = {"downloaded": [], "current": [], "modified": [], "failed": []}
    for entry in sorted(entries, key=lambda e: e["name"]):
        name = entry["name"]
        path = os.path.join(ready_dir, name)
        if os.path.isfile(path):
            if upload.sha256_and_size(path)[0] == entry["sha256"]:
                results["current"].append(name)
                continue
            if name not in force:
                results["modified"].append(name)
                continue
        temp = os.path.join(ready_dir, f".{name}.part")
        print(f"downloading {name} ({entry['size']:,} bytes)", file=out, flush=True)
        try:
            error = fetch(root, entry, temp)
            if error is None and upload.sha256_and_size(temp)[0] != entry["sha256"]:
                error = "SHA-256 does not match manifest.json"
            if error is None:
                os.replace(temp, path)
                results["downloaded"].append(name)
            else:
                results["failed"].append(f"{name}: {error}")
        finally:
            if os.path.exists(temp):
                os.remove(temp)

    for name in results["downloaded"]:
        print(f"downloaded: {name}", file=out)
    for name in results["current"]:
        print(f"current: {name}", file=out)
    for name in results["modified"]:
        print(f"locally modified: {name} (kept; --force {name} overwrites it)", file=out)
    for message in results["failed"]:
        print(f"failed: {message}", file=out)
    return 1 if results["modified"] or results["failed"] else 0


# A Release download URL: https://github.com/<owner>/<repo>/releases/download/<tag>/<name>
RELEASE_URL = re.compile(r"^https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)$")


def release_digests(repo, tag):
    """Return ({asset name: SHA-256 hex}, None) for a published Release, or (None, error)."""
    try:
        release = upload.release_assets(repo, tag)
    except StopError as error:
        return None, str(error).splitlines()[0]
    if release is None:
        return None, f"Release {tag} not found"
    is_draft, digests = release
    if is_draft:
        return None, f"Release {tag} is a draft, not published"
    return digests, None


def verify(root, out=None):
    """Check every released entry against its Release asset digest and its source.

    Writes nothing but a temporary directory, removed afterwards. Unreleased
    entries (url null) are out of scope and only listed.
    """
    out = out or sys.stdout
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.exists(manifest_path):
        raise StopError("manifest.json not found")
    entries = sorted(upload.load_manifest(manifest_path)["files"], key=lambda e: e["name"])

    releases = {}  # (repo, tag) -> (digests, error), one gh call per Release
    ok, unreleased, failed = [], [], []
    scratch = tempfile.mkdtemp(prefix="verify-cord-")
    try:
        for entry in entries:
            name, url, source = entry["name"], entry.get("url"), entry.get("source")
            if not url:
                unreleased.append(name)
                continue
            match = RELEASE_URL.match(url)
            if not match:
                failed.append(f"{name}: {url}: not a GitHub Release download URL")
            else:
                repo, tag, asset = match.groups()
                if (repo, tag) not in releases:
                    releases[repo, tag] = release_digests(repo, tag)
                found, error = releases[repo, tag]
                if error:
                    failed.append(f"{name}: {url}: {error}")
                elif asset not in found:
                    failed.append(f"{name}: {url}: no such asset on Release {tag}")
                elif not found[asset]:
                    failed.append(f"{name}: {url}: the asset has no digest")
                elif found[asset] != entry["sha256"]:
                    failed.append(f"{name}: {url}: asset digest sha256:{found[asset]} "
                                  "does not match manifest.json")
                else:
                    ok.append(f"{name}: {url}")
            if source:
                print(f"downloading {source}", file=out, flush=True)
                found, error = upload.curl_sha256(source, os.path.join(scratch, name))
                if error is None and found != entry["sha256"]:
                    error = "SHA-256 does not match manifest.json"
                if error is None:
                    ok.append(f"{name}: {source}")
                else:
                    failed.append(f"{name}: {source}: {error}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    for message in ok:
        print(f"verified: {message}", file=out)
    for name in unreleased:
        print(f"unreleased, not checked: {name}", file=out)
    for message in failed:
        print(f"failed: {message}", file=out)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="append", default=[], metavar="NAME",
        help="overwrite this locally modified file (repeatable)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="download nothing into senzing-ready/; check each released entry's Release "
             "asset digest, and its source copy, against manifest.json",
    )
    parser.add_argument("--root", help="repository root (default: from git)")
    args = parser.parse_args(argv)
    if args.verify and args.force:
        parser.error("--force cannot be used with --verify")
    try:
        root = args.root or upload.git(os.getcwd(), "rev-parse", "--show-toplevel")
        return verify(root) if args.verify else run(root, args.force)
    except StopError as error:
        print(f"stopped, nothing {'checked' if args.verify else 'downloaded'}: {error}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
