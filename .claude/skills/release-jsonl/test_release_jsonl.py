"""Tests for release_jsonl.py, run against fake gh, oras and curl executables.

A real bare git repository stands in for origin. Run with:
  python3 -m unittest discover -s .claude/skills/release-jsonl -p 'test_*.py'
"""

import datetime
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_jsonl  # noqa: E402

REGISTRY = "ghcr.io/owner/repo"

# Releases and pull requests live in $FAKE_GH_STATE; a merge is a real
# merge commit pushed to the bare repository at $FAKE_ORIGIN.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, subprocess, sys, tempfile
    state_dir = os.environ["FAKE_GH_STATE"]
    path = os.path.join(state_dir, "state.json")
    state = json.load(open(path)) if os.path.exists(path) else {"releases": {}, "prs": []}
    args = sys.argv[1:]
    with open(os.path.join(state_dir, "calls.log"), "a") as log:
        log.write(json.dumps(args) + "\\n")
    def opt(name):
        return args[args.index(name) + 1] if name in args else None
    def save():
        json.dump(state, open(path, "w"))
    def assets(tag):
        directory = os.path.join(state_dir, "assets", tag)
        os.makedirs(directory, exist_ok=True)
        return directory
    def copy(tag, files):
        for f in files:
            if os.path.basename(f) == os.environ.get("FAKE_GH_FAIL_UPLOAD"):
                save()
                sys.exit("HTTP 502: upload failed")
            shutil.copy(f, assets(tag))
    files = [a for a in args if os.path.isabs(a) and os.path.isfile(a)]
    if args[:2] == ["release", "list"]:
        print(json.dumps([{"tagName": t, "isDraft": r["draft"]} for t, r in state["releases"].items()]))
    elif args[:2] == ["release", "create"]:
        state["releases"][args[2]] = {"draft": "--draft" in args, "notes": opt("--notes"),
                                      "title": opt("--title"), "target": None, "latest": False}
        copy(args[2], files)
        save()
    elif args[:2] == ["release", "view"]:
        tag = args[2]
        if opt("--json") == "assets":
            d = assets(tag)
            print(json.dumps({"assets": [{"name": n, "size": os.path.getsize(os.path.join(d, n))}
                                         for n in sorted(os.listdir(d))]}))
        else:
            print(json.dumps({"body": state["releases"][tag]["notes"]}))
    elif args[:2] == ["release", "upload"]:
        copy(args[2], files)
    elif args[:2] == ["release", "edit"]:
        release = state["releases"][args[2]]
        release.update(draft="--draft=false" not in args, target=opt("--target"),
                       latest="--latest" in args)
        subprocess.run(["git", "-C", os.environ["FAKE_ORIGIN"], "tag", args[2], opt("--target")],
                       check=True)
        save()
    elif args[:2] == ["pr", "create"]:
        number = len(state["prs"]) + 1
        url = f"https://github.com/Owner/Repo/pull/{number}"
        state["prs"].append({"number": number, "head": opt("--head"), "state": "OPEN",
                             "url": url, "mergeCommit": None})
        save()
        print(url)
    elif args[:2] == ["pr", "list"]:
        print(json.dumps([p for p in state["prs"] if p["head"] == opt("--head")]))
    elif args[:2] == ["pr", "merge"]:
        if os.environ.get("FAKE_GH_MERGE_FAIL"):
            sys.exit("GraphQL: Pull request is not mergeable: protected branch rules")
        pr = state["prs"][int(args[2]) - 1]
        work = tempfile.mkdtemp()
        git = lambda *a: subprocess.run(["git", "-C", work, *a], check=True,
                                        capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "clone", "-q", os.environ["FAKE_ORIGIN"], work], check=True)
        git("merge", "--no-ff", "-q", "-m", f"Merge pull request #{pr['number']}",
            f"origin/{pr['head']}")
        git("push", "-q", "origin", "main", f":{pr['head']}")
        pr.update(state="MERGED", mergeCommit={"oid": git("rev-parse", "HEAD")})
        shutil.rmtree(work)
        save()
    else:
        sys.exit("fake gh: unsupported " + " ".join(args))
    """
)

# Serves a Release asset only once its Release is published.
FAKE_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, sys
    state_dir = os.environ["FAKE_GH_STATE"]
    url = sys.argv[-1]
    tag, name = url.split("/releases/download/")[1].split("/")
    state = json.load(open(os.path.join(state_dir, "state.json")))
    path = os.path.join(state_dir, "assets", tag, name)
    if state["releases"].get(tag, {}).get("draft", True) or not os.path.exists(path):
        sys.exit(22)
    sys.stdout.buffer.write(open(path, "rb").read())
    """
)

# A registry in $FAKE_ORAS_STATE: refs hold manifests, blobs/ holds file contents.
FAKE_ORAS = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    state = os.environ["FAKE_ORAS_STATE"]
    args = sys.argv[1:]
    with open(os.path.join(state, "calls.log"), "a") as log:
        log.write(json.dumps(args) + "\\n")
    def path(ref):
        return os.path.join(state, ref.replace("/", "_").replace(":", "@"))
    if args[:2] == ["manifest", "fetch"]:
        if not os.path.exists(path(args[-1])):
            sys.exit(f"Error response from registry: failed to find {args[-1]}: not found")
        print(open(path(args[-1])).read())
    elif args[0] == "tag":
        repository = args[1].rsplit(":", 1)[0]
        shutil.copy(path(args[1]), path(f"{repository}:{args[2]}"))
    elif args[:2] == ["blob", "fetch"]:
        digest = args[-1].split("@sha256:")[1]
        shutil.copy(os.path.join(state, "blobs", digest), args[args.index("--output") + 1])
    else:
        sys.exit("fake oras: unsupported " + " ".join(args))
    """
)

FILE_A = b'{"RECORD_ID": "1"}\n'
FILE_B = b'{"RECORD_ID": "2"}\n{"RECORD_ID": "3"}\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    with open(path) as file:
        return file.read()


TODAY = datetime.datetime.now(datetime.timezone.utc).date().isoformat()


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.origin = os.path.join(self.tmp, "origin.git")
        self.root = os.path.join(self.tmp, "repo")
        self.gh_state = os.path.join(self.tmp, "gh")
        self.oras_state = os.path.join(self.tmp, "oras")
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.gh_state, os.path.join(self.oras_state, "blobs"), bin_dir):
            os.makedirs(directory)
        for name, body in (("gh", FAKE_GH), ("oras", FAKE_ORAS), ("curl", FAKE_CURL)):
            exe = os.path.join(bin_dir, name)
            with open(exe, "w") as file:
                file.write(body)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "FAKE_GH_STATE": self.gh_state,
            "FAKE_ORAS_STATE": self.oras_state,
            "FAKE_ORIGIN": self.origin,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            # Keep the user's git config (signing, hooks) out of the test repositories.
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

        # origin holds release v0.1.0 with a.jsonl; b.jsonl is uploaded, not released.
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True)
        subprocess.run(["git", "clone", "-q", self.origin, self.root], check=True,
                       capture_output=True)
        self.write(".gitignore", "/jsonl\n")
        self.write_manifest([self.entry("a.jsonl", FILE_A, "v0.1.0")])
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        self.git("tag", "v0.1.0")
        self.git("push", "-q", "origin", "main", "v0.1.0")
        self.seed_registry("a.jsonl", FILE_A)
        self.seed_registry("b.jsonl", FILE_B)
        os.makedirs(os.path.join(self.root, "jsonl"))
        with open(os.path.join(self.root, "jsonl", "b.jsonl"), "wb") as file:
            file.write(FILE_B)
        self.upload_b()

    # --- helpers ---

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, name, text):
        with open(os.path.join(self.root, name), "w") as file:
            file.write(text)

    def entry(self, name, data, tag):
        return {
            "name": name, "size": len(data), "sha256": sha(data), "tag": tag,
            "url": tag and f"https://github.com/Owner/Repo/releases/download/{tag}/{name}",
            "oci": f"{REGISTRY}/{name}",
        }

    def write_manifest(self, entries):
        self.write("manifest.json", json.dumps({"files": entries}, indent=2) + "\n")

    def upload_b(self):
        """The working-tree state /upload-jsonl leaves after adding b.jsonl."""
        self.write_manifest([self.entry("a.jsonl", FILE_A, "v0.1.0"),
                             self.entry("b.jsonl", FILE_B, None)])
        self.write("CHANGELOG.md", release_jsonl.upload.CHANGELOG_HEADER
                   + "\n## [Unreleased]\n\n### Added\n\n- `b.jsonl`\n")

    def seed_registry(self, name, data):
        ref = f"{REGISTRY}/{name}:sha256-{sha(data)}"
        with open(os.path.join(self.oras_state, ref.replace("/", "_").replace(":", "@")), "w") as f:
            json.dump({"layers": [{"digest": f"sha256:{sha(data)}", "size": len(data)}]}, f)
        with open(os.path.join(self.oras_state, "blobs", sha(data)), "wb") as file:
            file.write(data)

    def calls(self, tool):
        log = os.path.join(self.gh_state if tool == "gh" else self.oras_state, "calls.log")
        return [json.loads(line) for line in read(log).splitlines()] if os.path.exists(log) else []

    def gh_state_json(self):
        return json.loads(read(os.path.join(self.gh_state, "state.json")))

    def release(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = release_jsonl.main(["--root", self.root, "--repo", "Owner/Repo", *args])
        return code, out.getvalue() + err.getvalue()

    def origin_file(self, ref, name):
        return subprocess.run(["git", "-C", self.origin, "show", f"{ref}:{name}"], check=True,
                              capture_output=True, text=True).stdout

    # --- pure functions ---

    def test_versions(self):
        self.assertEqual(release_jsonl.parse_version("v0.1.0"), (0, 1, 0))
        self.assertEqual(release_jsonl.parse_version("1.20.3"), (1, 20, 3))
        self.assertIsNone(release_jsonl.parse_version("1.2.3-rc.1"))
        self.assertIsNone(release_jsonl.parse_version("01.2.3"))
        self.assertEqual(release_jsonl.bumped((0, 1, 0), "major"), (1, 0, 0))
        self.assertEqual(release_jsonl.bumped((0, 1, 4), "minor"), (0, 2, 0))
        self.assertEqual(release_jsonl.bumped((0, 1, 4), "patch"), (0, 1, 5))
        self.assertEqual(release_jsonl.bumped(None, "minor"), (0, 1, 0))

    def test_anchor_drops_brackets_and_dots(self):
        self.assertEqual(release_jsonl.github_anchor("[0.2.0] - 2026-10-28"), "020---2026-10-28")

    def test_suggest_bump(self):
        self.assertEqual(release_jsonl.suggest_bump({"Removed": ["x"], "Added": ["y"]}), "major")
        self.assertEqual(release_jsonl.suggest_bump({"Added": ["y"], "Changed": ["z"]}), "minor")
        self.assertEqual(release_jsonl.suggest_bump({"Changed": ["z"]}), "patch")

    def test_released_changelog(self):
        text = (release_jsonl.upload.CHANGELOG_HEADER
                + "\n## [Unreleased]\n\n### Added\n\n- `b.jsonl`\n\n"
                "## [0.1.0] - 2026-10-27\n\n### Added\n\n- `a.jsonl`\n\n"
                "[Unreleased]: https://github.com/Owner/Repo/compare/0.1.0...HEAD\n"
                "[0.1.0]: https://github.com/Owner/Repo/releases/tag/0.1.0\n")
        result = release_jsonl.released_changelog(
            text, "0.2.0", "2026-10-28", "0.1.0", "Owner", "Repo")
        self.assertEqual(result, release_jsonl.upload.CHANGELOG_HEADER + textwrap.dedent("""
            ## [Unreleased]

            ## [0.2.0] - 2026-10-28

            ### Added

            - `b.jsonl`

            ## [0.1.0] - 2026-10-27

            ### Added

            - `a.jsonl`

            [Unreleased]: https://github.com/Owner/Repo/compare/0.2.0...HEAD
            [0.2.0]: https://github.com/Owner/Repo/compare/0.1.0...0.2.0
            [0.1.0]: https://github.com/Owner/Repo/releases/tag/0.1.0
            """))

    # --- status ---

    def test_status_suggests_minor_after_v0_1_0(self):
        code, output = self.release("status")
        self.assertEqual(code, 0)
        self.assertIn("latest: 0.1.0 (tag v0.1.0)", output)
        self.assertIn("suggested bump: minor", output)
        self.assertIn("minor: 0.2.0", output)
        self.assertIn("next: prepare", output)

    def test_status_nothing_to_release(self):
        self.write_manifest([self.entry("a.jsonl", FILE_A, "v0.1.0")])
        os.remove(os.path.join(self.root, "CHANGELOG.md"))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn("nothing to release", output)

    def test_status_names_files_missing_from_ghcr(self):
        os.remove(os.path.join(self.oras_state, f"{REGISTRY}/b.jsonl:sha256-{sha(FILE_B)}"
                               .replace("/", "_").replace(":", "@")))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn("no ghcr.io sha256 tag for: b.jsonl", output)

    # --- prepare ---

    def test_prepare_builds_draft_branch_and_pr(self):
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 0, output)
        release = self.gh_state_json()["releases"]["0.2.0"]
        self.assertTrue(release["draft"])
        self.assertEqual(release["title"], "0.2.0")
        self.assertEqual(
            release["notes"],
            "See [CHANGELOG.md](https://github.com/Owner/Repo/blob/main/CHANGELOG.md"
            f"#020---{TODAY})",
        )
        assets = os.path.join(self.gh_state, "assets", "0.2.0")
        self.assertEqual(sorted(os.listdir(assets)), ["a.jsonl", "b.jsonl"])
        with open(os.path.join(assets, "a.jsonl"), "rb") as file:
            self.assertEqual(file.read(), FILE_A)  # fetched from ghcr.io

        manifest = json.loads(self.origin_file("release-0.2.0", "manifest.json"))
        for entry in manifest["files"]:
            self.assertEqual(entry["tag"], "0.2.0")
            self.assertEqual(entry["url"], "https://github.com/Owner/Repo/releases/download/"
                             f"0.2.0/{entry['name']}")
        changelog = self.origin_file("release-0.2.0", "CHANGELOG.md")
        self.assertIn(f"## [Unreleased]\n\n## [0.2.0] - {TODAY}\n\n### Added\n\n- `b.jsonl`",
                      changelog)
        self.assertIn("[0.2.0]: https://github.com/Owner/Repo/compare/v0.1.0...0.2.0", changelog)
        self.assertIn("pull request: https://github.com/Owner/Repo/pull/1", output)
        self.assertEqual([c for c in self.calls("oras") if c[0] == "tag"], [])

    def test_prepare_requires_bump(self):
        code, output = self.release("prepare")
        self.assertEqual(code, 2)
        self.assertIn("--bump", output)

    def test_prepare_refuses_other_changes(self):
        self.write("README.md", "edited\n")
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("other changes", output)
        self.assertFalse(os.path.exists(os.path.join(self.gh_state, "state.json")))

    def test_prepare_stops_when_version_is_published(self):
        with open(os.path.join(self.gh_state, "state.json"), "w") as file:
            json.dump({"releases": {"0.2.0": {"draft": False}}, "prs": []}, file)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("published Release or tag 0.2.0 already exists", output)
        self.assertEqual([c for c in self.calls("gh") if c[:2] == ["release", "create"]], [])

    def test_prepare_resumes_a_partial_draft(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL_UPLOAD": "b.jsonl"}):
            code, _ = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        code, output = self.release("prepare")
        self.assertEqual(code, 0, output)
        self.assertIn("resuming draft 0.2.0", output)
        uploads = [c for c in self.calls("gh") if c[:2] == ["release", "upload"]]
        self.assertEqual(len(uploads), 1)
        self.assertEqual([os.path.basename(a) for a in uploads[0] if a.endswith(".jsonl")],
                         ["b.jsonl"])
        self.assertIn("--clobber", uploads[0])
        self.assertEqual(sorted(os.listdir(os.path.join(self.gh_state, "assets", "0.2.0"))),
                         ["a.jsonl", "b.jsonl"])

    def test_status_after_prepare_resumes_with_publish(self):
        self.release("prepare", "--bump", "minor")
        code, output = self.release("status")
        self.assertEqual(code, 0)
        self.assertIn("draft: 0.2.0 (resume)", output)
        self.assertIn("next: publish", output)

    # --- publish ---

    def test_publish_merges_tags_publishes_and_verifies(self):
        self.release("prepare", "--bump", "minor")
        code, output = self.release("publish")
        self.assertEqual(code, 0, output)
        self.assertIn(["pr", "merge", "1", "--merge", "--delete-branch"], self.calls("gh"))
        merge = self.gh_state_json()["prs"][0]["mergeCommit"]["oid"]
        tags = [c for c in self.calls("oras") if c[0] == "tag"]
        self.assertEqual(tags, [
            ["tag", f"{REGISTRY}/a.jsonl:sha256-{sha(FILE_A)}", "0.2.0"],
            ["tag", f"{REGISTRY}/b.jsonl:sha256-{sha(FILE_B)}", "0.2.0"],
        ])
        release = self.gh_state_json()["releases"]["0.2.0"]
        self.assertEqual((release["draft"], release["target"], release["latest"]),
                         (False, merge, True))
        self.assertEqual(output.count("verified: "), 2)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), merge)

    def test_publish_blocked_merge_leaves_draft(self):
        self.release("prepare", "--bump", "minor")
        with mock.patch.dict(os.environ, {"FAKE_GH_MERGE_FAIL": "1"}):
            code, output = self.release("publish")
        self.assertEqual(code, 2)
        self.assertIn("protected branch", output)
        self.assertTrue(self.gh_state_json()["releases"]["0.2.0"]["draft"])
        self.assertEqual([c for c in self.calls("oras") if c[0] == "tag"], [])

    def test_publish_reports_a_mismatched_download(self):
        self.release("prepare", "--bump", "minor")
        with open(os.path.join(self.gh_state, "assets", "0.2.0", "a.jsonl"), "wb") as file:
            file.write(b"tampered\n")
        code, output = self.release("publish")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH: https://github.com/Owner/Repo/releases/download/0.2.0/a.jsonl",
                      output)

    def test_publish_without_draft_stops(self):
        code, output = self.release("publish")
        self.assertEqual(code, 2)
        self.assertIn("no draft Release", output)


if __name__ == "__main__":
    unittest.main()
