#!/usr/bin/env python3
"""Push new and changed senzing-ready/*.jsonl files to ghcr.io and record them.

Each file goes to its own ghcr.io repository, <registry>/<name>, under the tag
sha256-<hex>. manifest.json and the [Unreleased] section of CHANGELOG.md are
updated in the working tree; nothing is committed.

Exit codes: 0 success, 1 some files refused or failed, 2 stopped before
changing anything.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ARTIFACT_TYPE = "application/vnd.senzing.cord.v1"
MEDIA_TYPE = "application/x-ndjson"
MAX_SIZE = 2 * 1024**3  # GitHub Release asset limit
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*\.jsonl$")
CHUNK = 1024 * 1024

# Keep a Changelog section order.
SECTION_ORDER = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]

CHANGELOG_HEADER = """# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"""


class StopError(Exception):
    """Raised when the run must stop before changing anything."""


# --- Repository ---------------------------------------------------------------


def git(root, *args):
    result = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise StopError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def github_repo(root):
    """Return (owner, repo) from the origin remote, in the remote's case."""
    url = git(root, "remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise StopError(f"origin is not a GitHub repository: {url}")
    return match.group(1), match.group(2)


# --- Files --------------------------------------------------------------------


def working_dir(root):
    """Return the senzing-ready/ directory, stopping if only the old jsonl/ exists."""
    ready_dir = os.path.join(root, "senzing-ready")
    if os.path.isdir(os.path.join(root, "jsonl")) and not os.path.isdir(ready_dir):
        raise StopError("move your local files with: mv jsonl senzing-ready")
    return ready_dir


def sha256_and_size(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def validation_error(path, name, size):
    """Return why the file may not be pushed, or None."""
    if not NAME_PATTERN.match(name):
        return f"name must match {NAME_PATTERN.pattern}"
    if size > MAX_SIZE:
        return f"{size:,} bytes is over the 2 GiB limit"
    with open(path, "rb") as file:
        for number, line in enumerate(file, start=1):
            try:
                json.loads(line)
            except ValueError:
                return f"line {number} is not valid JSON"
    return None


# --- oras -----------------------------------------------------------------------


def oras(*args, cwd=None):
    try:
        return subprocess.run(
            ["oras", *args], capture_output=True, text=True, cwd=cwd, check=False
        )
    except FileNotFoundError:
        raise StopError("oras is not on PATH; install it from https://oras.land/")


def tag_exists(ref):
    """Return whether ref exists; raise StopError on an authentication failure."""
    result = oras("manifest", "fetch", "--descriptor", ref)
    if result.returncode == 0:
        return True
    error = result.stderr.strip()
    if re.search(r"unauthorized|denied", error, re.IGNORECASE):
        raise StopError(
            f"cannot read {ref}: {error}\n"
            "Log in with: gh auth token | oras login ghcr.io -u <user> --password-stdin"
        )
    if error.endswith("not found"):
        return False
    raise StopError(f"cannot read {ref}: {error}")


def check_write_scope():
    """Stop if the gh token's classic scopes lack write:packages."""
    try:
        result = subprocess.run(
            ["gh", "api", "-i", "user"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return
    for line in result.stdout.splitlines():
        if line.lower().startswith("x-oauth-scopes:"):
            scopes = {s.strip() for s in line.split(":", 1)[1].split(",")}
            if "write:packages" not in scopes:
                raise StopError(
                    "the gh token lacks write:packages; run:\n"
                    "  gh auth refresh -h github.com -s write:packages\n"
                    "  gh auth token | oras login ghcr.io -u <user> --password-stdin"
                )
            return


def push(ready_dir, ref, name, sha256, size, source_url):
    """Push one file and confirm the registry holds it as a single layer."""
    result = oras(
        "push", ref,
        "--artifact-type", ARTIFACT_TYPE,
        "--annotation", f"org.opencontainers.image.source={source_url}",
        f"{name}:{MEDIA_TYPE}",
        cwd=ready_dir,
    )
    if result.returncode != 0:
        return result.stderr.strip() or "oras push failed"
    fetched = oras("manifest", "fetch", ref)
    if fetched.returncode != 0:
        return f"pushed, but cannot read it back: {fetched.stderr.strip()}"
    layers = json.loads(fetched.stdout).get("layers", [])
    if [(l.get("digest"), l.get("size")) for l in layers] != [(f"sha256:{sha256}", size)]:
        return "pushed, but the registry's layer does not match the file"
    return None


# --- manifest.json ------------------------------------------------------------


def load_manifest(path):
    if not os.path.exists(path):
        return {"files": []}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def write_manifest(path, manifest):
    manifest["files"].sort(key=lambda entry: entry["name"])
    with open(path, "w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")


# --- CHANGELOG.md -------------------------------------------------------------


def file_line(name, size=None, sha256=None):
    if size is None:
        return f"- `{name}`"
    return f"- `{name}` ({size:,} bytes, SHA-256 `{sha256}`)"


def line_name(line):
    match = re.match(r"- `([^`]+)`", line)
    return match.group(1) if match else None


def split_unreleased(text):
    """Split CHANGELOG text into (before, unreleased body lines, after)."""
    lines = text.splitlines()
    start = next(
        (i for i, l in enumerate(lines) if re.match(r"##\s+\[Unreleased\]", l, re.I)),
        None,
    )
    if start is None:
        insert = next(
            (i for i, l in enumerate(lines) if re.match(r"##\s+\[", l)),
            None,
        )
        if insert is None:
            insert = next(
                (i for i, l in enumerate(lines) if re.match(r"\[[^\]]+\]:\s", l)),
                len(lines),
            )
        return lines[:insert], [], lines[insert:]
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if re.match(r"##\s", lines[i]) or re.match(r"\[[^\]]+\]:\s", lines[i])
        ),
        len(lines),
    )
    return lines[:start], lines[start + 1 : end], lines[end:]


def parse_sections(body):
    """Return (preamble lines, {section: [lines]}) for the Unreleased body."""
    preamble, sections, current = [], {}, None
    for line in body:
        match = re.match(r"###\s+(\w+)", line)
        if match:
            current = match.group(1).capitalize()
            sections.setdefault(current, [])
        elif current is None:
            if line.strip():
                preamble.append(line)
        elif line.strip():
            sections[current].append(line)
    return preamble, sections


def record_change(sections, kind, name, line):
    """Apply one Added/Changed/Removed change, keeping one line per file."""
    where = {
        section: index
        for section, lines in sections.items()
        for index, existing in enumerate(lines)
        if line_name(existing) == name
    }
    if kind == "Changed" and "Added" in where:
        sections["Added"][where["Added"]] = line
        return
    if kind == "Removed" and "Added" in where:
        # Added and removed within one release: nothing to report.
        del sections["Added"][where["Added"]]
        return
    for section, index in sorted(where.items(), key=lambda item: -item[1]):
        del sections[section][index]
    if kind == "Added" and "Removed" in where:
        kind = "Changed"
    sections.setdefault(kind, []).append(line)


def update_changelog(path, changes):
    """changes: list of (kind, name, line) tuples."""
    if not changes:
        return
    text = CHANGELOG_HEADER
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            text = file.read()
    before, body, after = split_unreleased(text)
    preamble, sections = parse_sections(body)
    for kind, name, line in changes:
        record_change(sections, kind, name, line)

    out = list(before)
    while out and not out[-1].strip():
        out.pop()
    out += ["", "## [Unreleased]", ""]
    out += preamble + ([""] if preamble else [])
    ordered = SECTION_ORDER + sorted(set(sections) - set(SECTION_ORDER))
    for section in ordered:
        if sections.get(section):
            out += [f"### {section}", ""] + sections[section] + [""]
    rest = list(after)
    while rest and not rest[0].strip():
        rest.pop(0)
    out += rest
    while out and not out[-1].strip():
        out.pop()
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(out) + "\n")


# --- Main ---------------------------------------------------------------------


def plan(root, manifest, registry, removals):
    """Classify every senzing-ready/*.jsonl file. Returns (pushes, current, refused)."""
    entries = {entry["name"]: entry for entry in manifest["files"]}
    ready_dir = working_dir(root)
    names = sorted(
        n
        for n in (os.listdir(ready_dir) if os.path.isdir(ready_dir) else [])
        if n.endswith(".jsonl") and os.path.isfile(os.path.join(ready_dir, n))
    )
    pushes, current, refused = [], [], []
    for name in names:
        if name in removals:
            continue
        path = os.path.join(ready_dir, name)
        sha256, size = sha256_and_size(path)
        entry = entries.get(name)
        if entry is None:
            kind = "Added"
        elif entry["sha256"] != sha256:
            kind = "Changed"
        elif NAME_PATTERN.match(name) and tag_exists(f"{registry}/{name}:sha256-{sha256}"):
            current.append(name)
            continue
        else:
            kind = "Repair"
        error = validation_error(path, name, size)
        if error:
            refused.append((name, error))
        else:
            pushes.append({"kind": kind, "name": name, "sha256": sha256, "size": size})
    return pushes, current, refused


def run(root, removals, dry_run, out=None):
    out = out or sys.stdout
    manifest_path = os.path.join(root, "manifest.json")
    changelog_path = os.path.join(root, "CHANGELOG.md")
    ready_dir = working_dir(root)

    owner, repo = github_repo(root)
    registry = f"ghcr.io/{owner}/{repo}".lower()
    source_url = f"https://github.com/{owner}/{repo}"
    manifest = load_manifest(manifest_path)
    entries = {entry["name"]: entry for entry in manifest["files"]}

    unknown = [name for name in removals if name not in entries]
    if unknown:
        raise StopError(f"not in manifest.json, cannot remove: {', '.join(unknown)}")

    pushes, current, refused = plan(root, manifest, registry, set(removals))
    if pushes and not dry_run:
        check_write_scope()
        # Stops on an authentication failure before anything is pushed.
        tag_exists(f"{registry}/{pushes[0]['name']}:sha256-{pushes[0]['sha256']}")

    pushed, failed = [], []
    for item in pushes:
        ref = f"{registry}/{item['name']}:sha256-{item['sha256']}"
        if dry_run:
            pushed.append(item)
            continue
        print(f"pushing {item['name']} ({item['size']:,} bytes)", file=out, flush=True)
        error = push(ready_dir, ref, item["name"], item["sha256"], item["size"], source_url)
        if error:
            failed.append((item["name"], error))
        else:
            pushed.append(item)

    changes = []
    for item in pushed:
        name, oci = item["name"], f"{registry}/{item['name']}"
        if item["kind"] == "Added":
            entries[name] = {
                "name": name, "size": item["size"], "sha256": item["sha256"],
                "tag": None, "url": None, "oci": oci,
            }
        elif item["kind"] == "Changed":
            entries[name].update(
                size=item["size"], sha256=item["sha256"], tag=None, url=None, oci=oci
            )
        else:
            entries[name]["oci"] = oci
        if item["kind"] != "Repair":
            changes.append(
                (item["kind"], name, file_line(name, item["size"], item["sha256"]))
            )
    for name in removals:
        del entries[name]
        changes.append(("Removed", name, file_line(name)))

    if not dry_run and (pushed or removals):
        manifest["files"] = list(entries.values())
        write_manifest(manifest_path, manifest)
        update_changelog(changelog_path, changes)

    verb = "would push" if dry_run else "pushed"
    for item in pushed:
        print(f"{verb}: {item['name']} ({item['kind'].lower()})", file=out)
    for name in removals:
        print(f"{'would remove' if dry_run else 'removed'}: {name} (manifest entry only)", file=out)
    for name in current:
        print(f"current: {name}", file=out)
    for name, error in refused:
        print(f"refused: {name}: {error}", file=out)
    for name, error in failed:
        print(f"failed: {name}: {error}", file=out)
    if not (pushed or removals or current or refused or failed):
        print("no .jsonl files in senzing-ready/ and nothing to remove", file=out)
    return 1 if refused or failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--remove", action="append", default=[], metavar="NAME",
        help="remove this manifest entry (ghcr.io and Releases are not touched)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would be pushed or removed; change nothing",
    )
    parser.add_argument("--root", help="repository root (default: from git)")
    args = parser.parse_args(argv)
    try:
        root = args.root or git(os.getcwd(), "rev-parse", "--show-toplevel")
        return run(root, args.remove, args.dry_run)
    except StopError as error:
        print(f"stopped, nothing changed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
