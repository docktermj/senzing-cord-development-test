#!/usr/bin/env python3
"""Download every file in manifest.json into jsonl/, checking each SHA-256.

Released entries come from their public Release URL; unreleased entries (url
null) come from ghcr.io with oras. A current file is skipped, and a file whose
SHA-256 differs is kept unless named with --force.

Exit codes: 0 jsonl/ matches the manifest, 1 some entries failed or are
locally modified, 2 stopped before downloading anything.
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "upload-jsonl")
)
import upload_jsonl as upload  # noqa: E402
from upload_jsonl import StopError  # noqa: E402


def fetch(entry, target):
    """Download entry to target; return an error message, or None."""
    if entry.get("url"):
        command = ["curl", "-fsSL", "-o", target, entry["url"]]
    else:
        command = ["oras", "blob", "fetch", "--output", target,
                   f"{entry['oci']}@sha256:{entry['sha256']}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return f"{command[0]} is not on PATH"
    if result.returncode == 0:
        return None
    error = result.stderr.strip() or f"{command[0]} exited with {result.returncode}"
    if command[0] == "oras" and re.search(r"unauthorized|denied", error, re.IGNORECASE):
        error += "\nLog in with: gh auth token | oras login ghcr.io -u <user> --password-stdin"
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

    jsonl_dir = os.path.join(root, "jsonl")
    os.makedirs(jsonl_dir, exist_ok=True)
    results = {"downloaded": [], "current": [], "modified": [], "failed": []}
    for entry in sorted(entries, key=lambda e: e["name"]):
        name = entry["name"]
        path = os.path.join(jsonl_dir, name)
        if os.path.isfile(path):
            if upload.sha256_and_size(path)[0] == entry["sha256"]:
                results["current"].append(name)
                continue
            if name not in force:
                results["modified"].append(name)
                continue
        temp = os.path.join(jsonl_dir, f".{name}.part")
        print(f"downloading {name} ({entry['size']:,} bytes)", file=out, flush=True)
        try:
            error = fetch(entry, temp)
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
