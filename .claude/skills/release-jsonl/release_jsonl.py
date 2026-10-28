#!/usr/bin/env python3
"""Publish a SemVer release of the CORD JSONL files.

The release-jsonl skill runs the subcommands in order, asking the user between them:

  status            report the version, the suggested bump, a draft to resume, and blockers
  prepare --bump B  draft Release with every file; branch, commit, push and PR
  publish           merge the PR, tag ghcr.io, publish the draft, verify downloads

Every step can be rerun. A draft Release above the latest version is resumed.
Exit codes: 0 success, 1 verification failed, 2 stopped.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "upload-jsonl")
)
import upload_jsonl as upload  # noqa: E402
from upload_jsonl import StopError  # noqa: E402

BASE = "main"
VERSION_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CHANGELOG_FILES = {"manifest.json", "CHANGELOG.md"}
REF_DEF = re.compile(r"^\[([^\]]+)\]:\s")


# --- Commands -----------------------------------------------------------------


def run(*args, cwd=None):
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    if result.returncode != 0:
        raise StopError(f"{' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def gh_json(root, *args):
    return json.loads(run("gh", *args, cwd=root) or "null")


# --- Versions -----------------------------------------------------------------


def parse_version(tag):
    match = VERSION_PATTERN.match(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def format_version(version):
    return ".".join(str(part) for part in version)


def bumped(version, kind):
    major, minor, patch = version or (0, 0, 0)
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def latest_tag(root):
    """Return (version, tag name) of the highest SemVer tag, or (None, None)."""
    tags = [t for t in upload.git(root, "tag", "-l").splitlines() if parse_version(t)]
    if not tags:
        return None, None
    tag = max(tags, key=parse_version)
    return parse_version(tag), tag


# --- CHANGELOG.md -------------------------------------------------------------


def read_changelog(root):
    path = os.path.join(root, "CHANGELOG.md")
    if not os.path.exists(path):
        return upload.CHANGELOG_HEADER
    with open(path, encoding="utf-8") as file:
        return file.read()


def unreleased_sections(text):
    _, body, _ = upload.split_unreleased(text)
    _, sections = upload.parse_sections(body)
    return {name: lines for name, lines in sections.items() if lines}


def suggest_bump(sections):
    if sections.get("Removed"):
        return "major"
    if sections.get("Added"):
        return "minor"
    return "patch"


def github_anchor(heading):
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    slug = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return slug.replace(" ", "-")


def release_heading(version, date):
    return f"[{version}] - {date}"


def release_notes(owner, repo, version, date):
    anchor = github_anchor(release_heading(version, date))
    return (
        f"See [CHANGELOG.md](https://github.com/{owner}/{repo}/blob/{BASE}"
        f"/CHANGELOG.md#{anchor})"
    )


def released_changelog(text, version, date, previous_tag, owner, repo):
    """Turn [Unreleased] into [version] - date under a new empty [Unreleased]."""
    before, body, after = upload.split_unreleased(text)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    while before and not before[-1].strip():
        before.pop()
    lines = before + ["", "## [Unreleased]", "", f"## {release_heading(version, date)}", ""]
    lines += body + ([""] if body else []) + after

    content = [line for line in lines if not REF_DEF.match(line)]
    refs = [
        line
        for line in lines
        if REF_DEF.match(line)
        and REF_DEF.match(line).group(1).lower() not in ("unreleased", version)
    ]
    base_url = f"https://github.com/{owner}/{repo}"
    new_refs = [f"[Unreleased]: {base_url}/compare/{version}...HEAD"]
    if previous_tag:
        new_refs.append(f"[{version}]: {base_url}/compare/{previous_tag}...{version}")
    else:
        new_refs.append(f"[{version}]: {base_url}/releases/tag/{version}")
    while content and not content[-1].strip():
        content.pop()
    return "\n".join(content + [""] + new_refs + refs) + "\n"


# --- State --------------------------------------------------------------------


class Context:
    def __init__(self, root, repo=None):
        self.root = root
        if repo:
            self.owner, self.repo = repo.split("/", 1)
        else:
            self.owner, self.repo = upload.github_repo(root)
        upload.git(root, "fetch", "--quiet", "--tags", "origin")
        self.latest, self.latest_tag = latest_tag(root)
        self.manifest = upload.load_manifest(os.path.join(root, "manifest.json"))
        self.releases = gh_json(
            root, "release", "list", "--limit", "200", "--json", "tagName,isDraft"
        )

    def asset_url(self, version, name):
        return f"https://github.com/{self.owner}/{self.repo}/releases/download/{version}/{name}"

    def draft(self):
        """Return the version of a draft Release above the latest version, or None."""
        drafts = [
            r["tagName"]
            for r in self.releases
            if r["isDraft"]
            and parse_version(r["tagName"])
            and not r["tagName"].startswith("v")
            and (self.latest is None or parse_version(r["tagName"]) > self.latest)
        ]
        if len(drafts) > 1:
            raise StopError(f"more than one draft Release to resume: {', '.join(drafts)}")
        return drafts[0] if drafts else None

    def check_unused(self, version):
        published = [r for r in self.releases if r["tagName"] == version and not r["isDraft"]]
        if published or version in upload.git(self.root, "tag", "-l").splitlines():
            raise StopError(f"a published Release or tag {version} already exists")

    def pending_entries(self):
        latest = self.latest
        return [
            e["name"]
            for e in self.manifest["files"]
            if not e.get("tag") or parse_version(e["tag"]) != latest
        ]

    def missing_blobs(self):
        return [
            e["name"]
            for e in self.manifest["files"]
            if not upload.tag_exists(f"{e['oci']}:sha256-{e['sha256']}")
        ]

    def pull_request(self, version):
        prs = gh_json(
            self.root, "pr", "list", "--head", f"release-{version}", "--state", "all",
            "--json", "number,state,url,mergeCommit",
        )
        return prs[0] if prs else None


def blockers(ctx):
    problems = []
    if not unreleased_sections(read_changelog(ctx.root)) and not ctx.pending_entries():
        problems.append("nothing to release: [Unreleased] is empty and every file is released")
    if not ctx.manifest["files"]:
        problems.append("manifest.json lists no files")
    missing = ctx.missing_blobs()
    if missing:
        problems.append(f"no ghcr.io sha256 tag for: {', '.join(missing)}")
    return problems


def check_tree(root):
    for line in upload.git(root, "status", "--porcelain").splitlines():
        # upload.git strips the output, so the status column width is not fixed.
        if line.split(None, 1)[1] not in CHANGELOG_FILES:
            raise StopError(f"working tree has other changes: {line}")
    if upload.git(root, "branch", "--show-current") != BASE:
        raise StopError(f"check out {BASE} first")
    if upload.git(root, "rev-parse", BASE) != upload.git(root, "rev-parse", f"origin/{BASE}"):
        raise StopError(f"{BASE} is not the same as origin/{BASE}; pull or push first")


# --- Steps --------------------------------------------------------------------


def status(ctx, out):
    draft = ctx.draft()
    sections = unreleased_sections(read_changelog(ctx.root))
    print(f"latest: {format_version(ctx.latest) if ctx.latest else 'none'}"
          f" (tag {ctx.latest_tag or 'none'})", file=out)
    counts = ", ".join(f"{name} {len(lines)}" for name, lines in sections.items()) or "empty"
    print(f"unreleased: {counts}", file=out)
    if draft:
        pr = ctx.pull_request(draft)
        merged = pr and pr["state"] == "MERGED"
        print(f"draft: {draft} (resume)", file=out)
        print(f"next: {'publish' if merged or pr else 'prepare'}", file=out)
        return 0
    suggestion = suggest_bump(sections)
    print(f"suggested bump: {suggestion}", file=out)
    for kind in ("major", "minor", "patch"):
        print(f"  {kind}: {format_version(bumped(ctx.latest, kind))}", file=out)
    problems = blockers(ctx)
    for problem in problems:
        print(f"blocking: {problem}", file=out)
    print(f"next: {'stop' if problems else 'prepare --bump <major|minor|patch>'}", file=out)
    return 2 if problems else 0


def gather_assets(ctx, entries, directory):
    """Copy or fetch each entry's file into directory, checking each SHA-256."""
    paths = []
    for entry in entries:
        local = os.path.join(ctx.root, "jsonl", entry["name"])
        target = os.path.join(directory, entry["name"])
        if os.path.isfile(local) and upload.sha256_and_size(local)[0] == entry["sha256"]:
            shutil.copyfile(local, target)
        else:
            run("oras", "blob", "fetch", "--output", target,
                f"{entry['oci']}@sha256:{entry['sha256']}")
        if upload.sha256_and_size(target)[0] != entry["sha256"]:
            raise StopError(f"{entry['name']}: SHA-256 does not match manifest.json")
        paths.append(target)
    return paths


def draft_date(ctx, version):
    """Return the CHANGELOG date recorded in an existing draft's notes."""
    notes = gh_json(ctx.root, "release", "view", version, "--json", "body")["body"]
    match = re.search(r"---(\d{4}-\d{2}-\d{2})\)", notes)
    if not match:
        raise StopError(f"draft {version} has unexpected notes: {notes}")
    return match.group(1)


def prepare(ctx, bump, out):
    version = ctx.draft()
    if version:
        print(f"resuming draft {version}", file=out)
        date = draft_date(ctx, version)
    else:
        if not bump:
            raise StopError("--bump major|minor|patch is required for a new release")
        version = format_version(bumped(ctx.latest, bump))
        date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    ctx.check_unused(version)

    pr = ctx.pull_request(version)
    if pr:
        print(f"pull request: {pr['url']} ({pr['state'].lower()})", file=out)
        return 0

    branch = f"release-{version}"
    local_branch = bool(upload.git(ctx.root, "branch", "--list", branch))
    remote_branch = bool(upload.git(ctx.root, "branch", "-r", "--list", f"origin/{branch}"))
    if not (local_branch or remote_branch):
        check_tree(ctx.root)
        problems = blockers(ctx)
        if problems:
            raise StopError("; ".join(problems))

    # Step 3: draft Release holding every manifest file.
    with tempfile.TemporaryDirectory() as directory:
        if not ctx.draft():
            paths = gather_assets(ctx, ctx.manifest["files"], directory)
            run("gh", "release", "create", version, "--draft", "--title", version,
                "--notes", release_notes(ctx.owner, ctx.repo, version, date), *paths,
                cwd=ctx.root)
        else:
            assets = gh_json(ctx.root, "release", "view", version, "--json", "assets")
            have = {(a["name"], a["size"]) for a in assets["assets"]}
            want = [e for e in ctx.manifest["files"] if (e["name"], e["size"]) not in have]
            if want:
                paths = gather_assets(ctx, want, directory)
                run("gh", "release", "upload", version, *paths, "--clobber", cwd=ctx.root)
    print(f"draft: {version} holds every file", file=out)

    # Step 4: manifest and CHANGELOG on a branch, then the PR.
    if not (local_branch or remote_branch):
        manifest_path = os.path.join(ctx.root, "manifest.json")
        manifest = upload.load_manifest(manifest_path)
        for entry in manifest["files"]:
            entry["tag"] = version
            entry["url"] = ctx.asset_url(version, entry["name"])
        upload.git(ctx.root, "switch", "--quiet", "-c", branch)
        upload.write_manifest(manifest_path, manifest)
        text = released_changelog(
            read_changelog(ctx.root), version, date, ctx.latest_tag, ctx.owner, ctx.repo
        )
        with open(os.path.join(ctx.root, "CHANGELOG.md"), "w", encoding="utf-8") as file:
            file.write(text)
        upload.git(ctx.root, "add", "manifest.json", "CHANGELOG.md")
        upload.git(ctx.root, "commit", "--quiet", "-m", f"chore(release): {version}")
    if not remote_branch:
        upload.git(ctx.root, "push", "--quiet", "-u", "origin", branch)
    url = run(
        "gh", "pr", "create", "--base", BASE, "--head", branch,
        "--title", f"chore(release): {version}",
        "--body", f"Release {version}: updates `manifest.json` and `CHANGELOG.md`.\n\n"
        f"Draft Release {version} holds every file; it is published after this merges.",
        cwd=ctx.root,
    )
    print(f"pull request: {url}", file=out)
    return 0


def sha256_of_url(url):
    digest = hashlib.sha256()
    with subprocess.Popen(["curl", "-fsSL", url], stdout=subprocess.PIPE) as curl:
        while chunk := curl.stdout.read(upload.CHUNK):
            digest.update(chunk)
    if curl.returncode != 0:
        return None
    return digest.hexdigest()


def publish(ctx, out):
    version = ctx.draft()
    if not version:
        raise StopError("no draft Release to publish; run prepare first")
    pr = ctx.pull_request(version)
    if not pr:
        raise StopError(f"no pull request for release-{version}; run prepare first")

    # Step 5: merge.
    if pr["state"] == "OPEN":
        run("gh", "pr", "merge", str(pr["number"]), "--merge", "--delete-branch",
            cwd=ctx.root)
        pr = ctx.pull_request(version)
    if pr["state"] != "MERGED":
        raise StopError(f"pull request {pr['url']} is {pr['state'].lower()}, not merged")
    sha = pr["mergeCommit"]["oid"]
    print(f"merged: {pr['url']} as {sha}", file=out)

    upload.git(ctx.root, "fetch", "--quiet", "origin")
    manifest = json.loads(upload.git(ctx.root, "show", f"{sha}:manifest.json"))

    # Step 6: ghcr.io version tags.
    for entry in manifest["files"]:
        run("oras", "tag", f"{entry['oci']}:sha256-{entry['sha256']}", version)
        print(f"tagged: {entry['oci']}:{version}", file=out)

    # Step 7: publish the draft on the merge commit.
    run("gh", "release", "edit", version, "--draft=false", "--target", sha, "--latest",
        cwd=ctx.root)
    print(f"published: {version} at {sha}", file=out)

    if upload.git(ctx.root, "status", "--porcelain") == "":
        upload.git(ctx.root, "switch", "--quiet", BASE)
        upload.git(ctx.root, "merge", "--quiet", "--ff-only", f"origin/{BASE}")

    # Step 8: verify every Release URL.
    failed = 0
    for entry in manifest["files"]:
        actual = sha256_of_url(entry["url"])
        ok = actual == entry["sha256"]
        failed += not ok
        print(f"{'verified' if ok else 'MISMATCH'}: {entry['url']}", file=out)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="repository root (default: from git)")
    parser.add_argument("--repo", help="owner/name (default: from the origin remote)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--bump", choices=["major", "minor", "patch"])
    commands.add_parser("publish")
    args = parser.parse_args(argv)
    out = sys.stdout
    try:
        root = args.root or upload.git(os.getcwd(), "rev-parse", "--show-toplevel")
        ctx = Context(root, args.repo)
        if args.command == "status":
            return status(ctx, out)
        if args.command == "prepare":
            return prepare(ctx, args.bump, out)
        return publish(ctx, out)
    except StopError as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
