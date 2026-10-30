#!/usr/bin/env python3
"""Download every file in manifest.json into senzing-ready/, checking each SHA-256.

Released entries come from their public Release URL; unreleased entries (url
null) come from the "unreleased" staging draft Release with gh. A current file
is skipped, and a file whose SHA-256 differs is kept unless named with --force.

Exit codes: 0 senzing-ready/ matches the manifest, 1 some entries failed or are
locally modified, 2 stopped before downloading anything.
"""

import argparse
import os
import re
import subprocess
import sys

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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="append", default=[], metavar="NAME",
        help="overwrite this locally modified file (repeatable)",
    )
    parser.add_argument("--root", help="repository root (default: from git)")
    args = parser.parse_args(argv)
    try:
        root = args.root or upload.git(os.getcwd(), "rev-parse", "--show-toplevel")
        return run(root, args.force)
    except StopError as error:
        print(f"stopped, nothing downloaded: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
