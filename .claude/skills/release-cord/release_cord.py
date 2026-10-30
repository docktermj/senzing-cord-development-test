#!/usr/bin/env python3
"""Publish a SemVer release of the CORD JSONL files.

The release-cord skill runs the subcommands in order, asking the user between them:

  upload            upload new and changed senzing-ready/ files (/upload-senzing-ready),
                    after printing a preview; skipped while a draft is being resumed
  status            report the version, the suggested bump, a draft to resume, and blockers
  prepare --bump B  turn the "unreleased" staging draft into the version's draft Release;
                    update README.md and CORD_stats.xlsx; branch, commit, push and PR
  publish           merge the PR, publish the draft, verify downloads

Every step can be rerun. A draft Release above the latest version is resumed.
Exit codes: 0 success, 1 upload refused or failed, or verification failed, 2 stopped.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
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

BASE = "main"
VERSION_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
CHANGELOG_FILES = {"manifest.json", "CHANGELOG.md"}
RELEASE_FILES = ("manifest.json", "CHANGELOG.md", "README.md", "CORD_stats.xlsx")
REF_DEF = re.compile(r"^\[([^\]]+)\]:\s")
README_SECTION = "## CORDs"
STATS_FILE = "CORD_stats.xlsx"
STATS_SCRIPT = os.path.join("bin", "get_cord_stats.py")


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


# --- README.md and CORD_stats.xlsx --------------------------------------------


def region_of(name):
    """Return the region of <source>-<region>.jsonl, which must have a README heading."""
    error = upload.region_error(name)
    if error:
        raise StopError(error)
    return name[: -len(".jsonl")].rsplit("-", 1)[1]


def readme_cords(text, entries):
    """Rewrite the file lists under ## CORDs: one ### section per region, sorted."""
    lines = text.splitlines()
    if README_SECTION not in lines:
        raise StopError(f"README.md has no {README_SECTION} section")
    start = lines.index(README_SECTION)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
    )
    intro = lines[start + 1:end]
    intro = intro[: next((i for i, line in enumerate(intro) if line.startswith("### ")),
                         len(intro))]
    while intro and not intro[-1].strip():
        intro.pop()
    section = [README_SECTION] + intro + [""]
    for region, heading in sorted(upload.REGIONS.items(), key=lambda item: item[1]):
        files = sorted(
            (e["name"], e["url"]) for e in entries if region_of(e["name"]) == region
        )
        section += [f"### {heading}", ""]
        section += [f"- [{name}]({url})" for name, url in files] or ["Not published yet."]
        section += [""]
    return "\n".join(lines[:start] + section + lines[end:]).rstrip("\n") + "\n"


def openpyxl_available():
    return importlib.util.find_spec("openpyxl") is not None


def update_stats(root, version, date, added, changed):
    """Run bin/get_cord_stats.py on the Added and on the Changed files."""
    for reason, paths in ((f"Added in {version}", added), (f"Changed in {version}", changed)):
        if paths:
            run(sys.executable, os.path.join(root, STATS_SCRIPT), "--stats",
                os.path.join(root, STATS_FILE), "--reason", reason, "--date", date, *paths,
                cwd=root)


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
        """New and changed files: /upload-senzing-ready sets their tag to null."""
        return [e["name"] for e in self.manifest["files"] if not e.get("tag")]

    def release_entries(self, version):
        """The files this release attaches: pending, or already tagged by a resumed run."""
        return [e for e in self.manifest["files"] if e.get("tag") in (None, version)]

    def missing_assets(self, tag):
        """Pending files that the draft Release tag lacks, or holds with another SHA-256."""
        release = upload.release_assets(f"{self.owner}/{self.repo}", tag)
        assets = release[1] if release else {}
        return [
            e["name"]
            for e in self.manifest["files"]
            if e.get("tag") in (None, tag) and assets.get(e["name"]) != e["sha256"]
        ]

    def pull_request(self, version):
        prs = gh_json(
            self.root, "pr", "list", "--head", f"release-{version}", "--state", "all",
            "--json", "number,state,url,mergeCommit",
        )
        return prs[0] if prs else None


def blockers(ctx):
    problems = []
    for entry in ctx.manifest["files"]:
        try:
            region_of(entry["name"])
        except StopError as error:
            problems.append(str(error))
    if ctx.pending_entries():
        if not openpyxl_available():
            problems.append(f"{STATS_FILE} needs openpyxl: pip install openpyxl")
        if not os.path.exists(os.path.join(ctx.root, STATS_FILE)):
            problems.append(f"{STATS_FILE} not found")
    readme = os.path.join(ctx.root, "README.md")
    if not os.path.exists(readme):
        problems.append("README.md not found")
    else:
        with open(readme, encoding="utf-8") as file:
            if README_SECTION not in file.read().splitlines():
                problems.append(f"README.md has no {README_SECTION} section")
    if not unreleased_sections(read_changelog(ctx.root)) and not ctx.pending_entries():
        problems.append("nothing to release: [Unreleased] is empty and every file is released")
    if not ctx.manifest["files"]:
        problems.append("manifest.json lists no files")
    if ctx.pending_entries() or ctx.draft():
        tag = ctx.draft() or upload.STAGING
        missing = ctx.missing_assets(tag)
        if missing:
            problems.append(f"not in draft Release {tag} with a matching digest: "
                            f"{', '.join(missing)}; run /upload-senzing-ready")
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


def upload_step(ctx, out):
    """Run /upload-senzing-ready: a preview, then the push unless a file is refused."""
    draft = ctx.draft()
    if draft:
        print(f"skipped: resuming draft {draft}", file=out)
        return 0
    repo = f"{ctx.owner}/{ctx.repo}"
    print("preview:", file=out)
    if upload.run(ctx.root, [], dry_run=True, out=out, repo=repo):
        print("stopped: fix or remove the refused files, then rerun", file=out)
        return 1
    print("upload:", file=out)
    return upload.run(ctx.root, [], dry_run=False, out=out, repo=repo)


def gather_assets(ctx, version, entries, directory):
    """Copy each entry's file into directory, from senzing-ready/ or the version's draft."""
    paths = []
    for entry in entries:
        local = os.path.join(ctx.root, "senzing-ready", entry["name"])
        target = os.path.join(directory, entry["name"])
        if os.path.isfile(local) and upload.sha256_and_size(local)[0] == entry["sha256"]:
            shutil.copyfile(local, target)
        else:
            run("gh", "release", "download", version, "--repo", f"{ctx.owner}/{ctx.repo}",
                "--pattern", entry["name"], "--output", target, "--clobber")
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

    # Step 3: the staging draft becomes the version's draft, holding only the new and
    # changed files. Renaming it moves the assets without uploading them again.
    entries = ctx.release_entries(version)
    repo = f"{ctx.owner}/{ctx.repo}"
    with tempfile.TemporaryDirectory() as directory:
        if not ctx.draft():
            if upload.staged_assets(ctx.root, repo) is None:
                names = ", ".join(e["name"] for e in entries) or "no files"
                raise StopError(f"no {upload.STAGING} draft Release holds the pending files "
                                f"({names}); run /upload-senzing-ready")
            run("gh", "release", "edit", upload.STAGING, "--repo", repo, "--tag", version,
                "--title", version, "--notes", release_notes(ctx.owner, ctx.repo, version, date))
            ctx.releases.append({"tagName": version, "isDraft": True})
        _, assets = upload.release_assets(repo, version)
        wanted = {e["name"] for e in entries}
        for name in sorted(set(assets) - wanted):
            run("gh", "release", "delete-asset", version, name, "--repo", repo, "--yes")
            print(f"deleted from draft: {name} (not a new or changed file)", file=out)
        missing = ctx.missing_assets(version)
        if missing:
            raise StopError(f"draft Release {version} lacks a matching digest for: "
                            f"{', '.join(missing)}")
        names = ", ".join(sorted(e["name"] for e in entries)) or "no files"
        print(f"draft: {version} holds {names}", file=out)

        # Step 4: stats, manifest, CHANGELOG and README on a branch, then the PR.
        if not (local_branch or remote_branch):
            # Stats come from the same bytes as the Release assets.
            paths = gather_assets(ctx, version, entries, directory)
            text = read_changelog(ctx.root)
            added_names = {upload.line_name(line)
                           for line in unreleased_sections(text).get("Added", [])}
            update_stats(
                ctx.root, version, date,
                [p for p in paths if os.path.basename(p) in added_names],
                [p for p in paths if os.path.basename(p) not in added_names],
            )
            manifest_path = os.path.join(ctx.root, "manifest.json")
            manifest = upload.load_manifest(manifest_path)
            for entry in manifest["files"]:
                if entry.get("tag") in (None, version):
                    entry["tag"] = version
                    entry["url"] = ctx.asset_url(version, entry["name"])
            upload.git(ctx.root, "switch", "--quiet", "-c", branch)
            upload.write_manifest(manifest_path, manifest)
            with open(os.path.join(ctx.root, "CHANGELOG.md"), "w", encoding="utf-8") as file:
                file.write(released_changelog(
                    text, version, date, ctx.latest_tag, ctx.owner, ctx.repo
                ))
            readme_path = os.path.join(ctx.root, "README.md")
            with open(readme_path, encoding="utf-8") as file:
                readme = readme_cords(file.read(), manifest["files"])
            with open(readme_path, "w", encoding="utf-8") as file:
                file.write(readme)
            upload.git(ctx.root, "add", *RELEASE_FILES)
            upload.git(ctx.root, "commit", "--quiet", "-m", f"chore(release): {version}")
    if not remote_branch:
        upload.git(ctx.root, "push", "--quiet", "-u", "origin", branch)
    url = run(
        "gh", "pr", "create", "--base", BASE, "--head", branch,
        "--title", f"chore(release): {version}",
        "--body", f"Release {version}: updates `manifest.json`, `CHANGELOG.md`, "
        f"`README.md` and `{STATS_FILE}`.\n\n"
        f"Draft Release {version} holds the new and changed files; it is published after "
        "this merges.",
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

    # Step 6: publish the draft on the merge commit.
    run("gh", "release", "edit", version, "--draft=false", "--target", sha, "--latest",
        cwd=ctx.root)
    print(f"published: {version} at {sha}", file=out)

    if upload.git(ctx.root, "status", "--porcelain") == "":
        upload.git(ctx.root, "switch", "--quiet", BASE)
        upload.git(ctx.root, "merge", "--quiet", "--ff-only", f"origin/{BASE}")

    # Step 7: verify the URL of every file this Release holds.
    failed = 0
    for entry in (e for e in manifest["files"] if e.get("tag") == version):
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
    commands.add_parser("upload")
    commands.add_parser("status")
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--bump", choices=["major", "minor", "patch"])
    commands.add_parser("publish")
    args = parser.parse_args(argv)
    out = sys.stdout
    try:
        root = args.root or upload.git(os.getcwd(), "rev-parse", "--show-toplevel")
        upload.working_dir(root)
        ctx = Context(root, args.repo)
        if args.command == "upload":
            return upload_step(ctx, out)
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
