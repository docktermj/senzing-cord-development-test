"""Tests for publish_cord.py, run against fake gh and curl executables.

A real bare git repository stands in for origin. Run with:
  python3 -m unittest discover -s .claude/skills/publish-cord -p 'test_*.py'
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
import publish_cord  # noqa: E402

STAGING = publish_cord.upload.STAGING

# Releases and pull requests live in $FAKE_GH_STATE; a merge is a real
# merge commit pushed to the bare repository at $FAKE_ORIGIN.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import hashlib, json, os, shutil, subprocess, sys, tempfile
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
        if tag not in state["releases"]:
            sys.exit("release not found")
        if opt("--json") == "isDraft,assets":
            d = assets(tag)
            found = []
            for n in sorted(os.listdir(d)):
                data = open(os.path.join(d, n), "rb").read()
                found.append({"name": n, "size": len(data),
                              "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
            print(json.dumps({"isDraft": state["releases"][tag]["draft"], "assets": found}))
        else:
            print(json.dumps({"body": state["releases"][tag]["notes"]}))
    elif args[:2] == ["release", "upload"]:
        copy(args[2], files)
    elif args[:2] == ["release", "delete-asset"]:
        os.remove(os.path.join(assets(args[2]), args[3]))
    elif args[:2] == ["release", "download"]:
        shutil.copy(os.path.join(assets(args[2]), opt("--pattern")), opt("--output"))
    elif args[:2] == ["release", "edit"] and "--tag" in args:
        # Renaming a draft: its assets move with it.
        release = state["releases"].pop(args[2])
        release.update(title=opt("--title"), notes=opt("--notes"))
        state["releases"][opt("--tag")] = release
        os.rename(assets(args[2]), os.path.join(state_dir, "assets", opt("--tag")))
        save()
    elif args[:2] == ["release", "edit"]:
        release = state["releases"][args[2]]
        release.update(draft="--draft=false" not in args, target=opt("--target"),
                       latest="--latest" in args)
        subprocess.run(["git", "-C", os.environ["FAKE_ORIGIN"], "tag", args[2], opt("--target")],
                       check=True)
        save()
    elif args[:2] == ["pr", "create"]:
        if os.environ.get("FAKE_GH_PR_FAIL"):
            sys.exit("HTTP 502: pull request not created")
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

# Stands in for bin/get_cord_stats.py: appends its arguments to the --stats file.
FAKE_STATS = textwrap.dedent(
    """\
    import os, sys
    args = sys.argv[1:]
    with open(args[args.index("--stats") + 1], "a") as stats:
        stats.write(" ".join(os.path.basename(a) for a in args) + "\\n")
    """
)

README = textwrap.dedent(
    """\
    # repo

    ## CORDs

    Intro stays.

    ### Old

    - [old.jsonl](https://example.com/old.jsonl)

    ## References

    - kept
    """
)

FILE_A = b'{"RECORD_ID": "1"}\n'
FILE_B = b'{"RECORD_ID": "2"}\n{"RECORD_ID": "3"}\n'
FILE_C = b'{"RECORD_ID": "4"}\n'


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
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.gh_state, bin_dir):
            os.makedirs(directory)
        for name, body in (("gh", FAKE_GH), ("curl", FAKE_CURL)):
            exe = os.path.join(bin_dir, name)
            with open(exe, "w") as file:
                file.write(body)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "FAKE_GH_STATE": self.gh_state,
            "FAKE_ORIGIN": self.origin,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
            # Keep the user's git config (signing, hooks) out of the test repositories.
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        stats_script = os.path.join(bin_dir, "get_cord_stats.py")
        with open(stats_script, "w") as file:
            file.write(FAKE_STATS)
        for patcher in (mock.patch.object(publish_cord, "STATS_SCRIPT", stats_script),
                        mock.patch.object(publish_cord, "openpyxl_available", lambda: True)):
            patcher.start()
            self.addCleanup(patcher.stop)

        # origin holds release v0.1.0 with a-london.jsonl; b-london.jsonl is staged, not released.
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin], check=True)
        subprocess.run(["git", "clone", "-q", self.origin, self.root], check=True,
                       capture_output=True)
        self.write(".gitignore", "/senzing-ready\n")
        self.write("README.md", README)
        self.write("CORD_stats.xlsx", "stats\n")
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0")])
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")
        self.git("tag", "v0.1.0")
        self.git("push", "-q", "origin", "main", "v0.1.0")
        self.stage("b-london.jsonl", FILE_B)
        os.makedirs(os.path.join(self.root, "senzing-ready"))
        with open(os.path.join(self.root, "senzing-ready", "b-london.jsonl"), "wb") as file:
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
        }

    def write_manifest(self, entries):
        self.write("manifest.json", json.dumps({"files": entries}, indent=2) + "\n")

    def upload_b(self):
        """The working-tree state /upload-senzing-ready leaves after adding b-london.jsonl."""
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0"),
                             self.entry("b-london.jsonl", FILE_B, None)])
        self.write("CHANGELOG.md", publish_cord.upload.CHANGELOG_HEADER
                   + "\n## [Unreleased]\n\n### Added\n\n- `b-london.jsonl`\n")

    def stage(self, name, data):
        """Put a file on the staging draft, as /upload-senzing-ready leaves it."""
        path = os.path.join(self.gh_state, "state.json")
        state = json.loads(read(path)) if os.path.exists(path) else {"releases": {}, "prs": []}
        state["releases"].setdefault(STAGING, {"draft": True, "notes": "", "title": STAGING,
                                               "target": None, "latest": False})
        with open(path, "w") as file:
            json.dump(state, file)
        directory = os.path.join(self.gh_state, "assets", STAGING)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "wb") as file:
            file.write(data)

    def calls(self):
        log = os.path.join(self.gh_state, "calls.log")
        return [json.loads(line) for line in read(log).splitlines()] if os.path.exists(log) else []

    def uploads(self):
        return [c for c in self.calls() if c[:2] == ["release", "upload"]]

    def gh_state_json(self):
        return json.loads(read(os.path.join(self.gh_state, "state.json")))

    def assertNoVersionDraft(self):
        self.assertEqual(list(self.gh_state_json()["releases"]), [STAGING])

    def release(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = publish_cord.main(["--root", self.root, "--repo", "Owner/Repo", *args])
        return code, out.getvalue() + err.getvalue()

    def origin_file(self, ref, name):
        return subprocess.run(["git", "-C", self.origin, "show", f"{ref}:{name}"], check=True,
                              capture_output=True, text=True).stdout

    # --- pure functions ---

    def test_old_jsonl_dir_alone_stops(self):
        shutil.move(os.path.join(self.root, "senzing-ready"), os.path.join(self.root, "jsonl"))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn("mv jsonl senzing-ready", output)
        self.assertEqual(self.calls(), [])

    def test_versions(self):
        self.assertEqual(publish_cord.parse_version("v0.1.0"), (0, 1, 0))
        self.assertEqual(publish_cord.parse_version("1.20.3"), (1, 20, 3))
        self.assertIsNone(publish_cord.parse_version("1.2.3-rc.1"))
        self.assertIsNone(publish_cord.parse_version("01.2.3"))
        self.assertEqual(publish_cord.bumped((0, 1, 0), "major"), (1, 0, 0))
        self.assertEqual(publish_cord.bumped((0, 1, 4), "minor"), (0, 2, 0))
        self.assertEqual(publish_cord.bumped((0, 1, 4), "patch"), (0, 1, 5))
        self.assertEqual(publish_cord.bumped(None, "minor"), (0, 1, 0))

    def test_anchor_drops_brackets_and_dots(self):
        self.assertEqual(publish_cord.github_anchor("[0.2.0] - 2026-10-28"), "020---2026-10-28")

    def test_suggest_bump(self):
        self.assertEqual(publish_cord.suggest_bump({"Removed": ["x"], "Added": ["y"]}), "major")
        self.assertEqual(publish_cord.suggest_bump({"Added": ["y"], "Changed": ["z"]}), "minor")
        self.assertEqual(publish_cord.suggest_bump({"Changed": ["z"]}), "patch")

    def test_readme_merges_regions_that_share_a_heading(self):
        entries = [{"name": name, "url": f"https://example.com/{name}"} for name in (
            "c-london.jsonl", "globaldata-london_central_a.jsonl", "a-london_central_a.jsonl",
            "b-london.jsonl")]
        result = publish_cord.readme_cords(README, entries)
        self.assertEqual(result.count("### London"), 1)
        self.assertEqual(result, textwrap.dedent("""\
            # repo

            ## CORDs

            Intro stays.

            ### Las Vegas

            Not published yet.

            ### London

            - [a-london_central_a.jsonl](https://example.com/a-london_central_a.jsonl)
            - [b-london.jsonl](https://example.com/b-london.jsonl)
            - [c-london.jsonl](https://example.com/c-london.jsonl)
            - [globaldata-london_central_a.jsonl](https://example.com/globaldata-london_central_a.jsonl)

            ### Moscow

            Not published yet.

            ## References

            - kept
            """))

    def test_readme_heading_without_files_says_not_published_once(self):
        entries = [{"name": "a-london_central_a.jsonl", "url": "https://example.com/a"}]
        result = publish_cord.readme_cords(README, entries)
        self.assertEqual(result.count("### London"), 1)
        self.assertIn("### London\n\n- [a-london_central_a.jsonl](https://example.com/a)\n\n"
                      "### Moscow", result)
        self.assertEqual(result.count("Not published yet."), 2)  # Las Vegas and Moscow
        self.assertIn("### Las Vegas\n\nNot published yet.\n\n### London", result)
        self.assertIn("### Moscow\n\nNot published yet.\n\n## References", result)

    def test_readme_for_the_current_manifest_is_unchanged(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        readme = read(os.path.join(root, "README.md"))
        entries = json.loads(read(os.path.join(root, "manifest.json")))["files"]
        self.assertEqual(publish_cord.readme_cords(readme, entries), readme)

    def test_released_changelog(self):
        text = (publish_cord.upload.CHANGELOG_HEADER
                + "\n## [Unreleased]\n\n### Added\n\n- `b-london.jsonl`\n\n"
                "## [0.1.0] - 2026-10-27\n\n### Added\n\n- `a-london.jsonl`\n\n"
                "[Unreleased]: https://github.com/Owner/Repo/compare/0.1.0...HEAD\n"
                "[0.1.0]: https://github.com/Owner/Repo/releases/tag/0.1.0\n")
        result = publish_cord.released_changelog(
            text, "0.2.0", "2026-10-28", "0.1.0", "Owner", "Repo")
        self.assertEqual(result, publish_cord.upload.CHANGELOG_HEADER + textwrap.dedent("""
            ## [Unreleased]

            ## [0.2.0] - 2026-10-28

            ### Added

            - `b-london.jsonl`

            ## [0.1.0] - 2026-10-27

            ### Added

            - `a-london.jsonl`

            [Unreleased]: https://github.com/Owner/Repo/compare/0.2.0...HEAD
            [0.2.0]: https://github.com/Owner/Repo/compare/0.1.0...0.2.0
            [0.1.0]: https://github.com/Owner/Repo/releases/tag/0.1.0
            """))

    # --- upload ---

    def write_ready(self, name, data):
        with open(os.path.join(self.root, "senzing-ready", name), "wb") as file:
            file.write(data)

    def test_upload_stages_a_new_file_then_status_offers_the_bump(self):
        self.write_ready("c-moscow.jsonl", FILE_C)
        code, output = self.release("upload")
        self.assertEqual(code, 0, output)
        self.assertLess(output.index("preview:"), output.index("uploaded: c-moscow.jsonl"))
        self.assertIn("would upload: c-moscow.jsonl (added)", output)
        self.assertEqual([os.path.basename(c[3]) for c in self.uploads()], ["c-moscow.jsonl"])
        self.assertEqual(self.uploads()[0][2], STAGING)
        manifest = json.loads(read(os.path.join(self.root, "manifest.json")))
        self.assertIn({"name": "c-moscow.jsonl", "tag": None},
                      [{"name": e["name"], "tag": e["tag"]} for e in manifest["files"]])
        self.assertIn("- `c-moscow.jsonl`", read(os.path.join(self.root, "CHANGELOG.md")))
        code, output = self.release("status")
        self.assertEqual(code, 0, output)
        self.assertIn("next: prepare", output)

    def test_upload_stops_on_a_refused_file_before_any_upload(self):
        self.write_ready("c-moscow.jsonl", FILE_C)
        self.write_ready("bob.jsonl", FILE_C)
        code, output = self.release("upload")
        self.assertEqual(code, 1)
        self.assertIn("refused: bob.jsonl: expected <source>-<region>.jsonl", output)
        self.assertNotIn("\nupload:", output)
        self.assertEqual(self.uploads(), [])

    def test_upload_with_nothing_new_then_nothing_to_release(self):
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0")])
        os.remove(os.path.join(self.root, "CHANGELOG.md"))
        os.remove(os.path.join(self.root, "senzing-ready", "b-london.jsonl"))
        code, output = self.release("upload")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.uploads(), [])
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn("nothing to release", output)

    def test_upload_is_skipped_while_resuming_a_draft(self):
        self.release("prepare", "--bump", "minor")
        self.git("switch", "-q", "main")
        self.write_ready("c-moscow.jsonl", FILE_C)
        code, output = self.release("upload")
        self.assertEqual(code, 0, output)
        self.assertIn("skipped: resuming draft 0.2.0", output)
        self.assertEqual(self.uploads(), [])

    # --- status ---

    def test_status_suggests_minor_after_v0_1_0(self):
        code, output = self.release("status")
        self.assertEqual(code, 0)
        self.assertIn("latest: 0.1.0 (tag v0.1.0)", output)
        self.assertIn("suggested bump: minor", output)
        self.assertIn("minor: 0.2.0", output)
        self.assertIn("next: prepare", output)

    def test_status_nothing_to_release(self):
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0")])
        os.remove(os.path.join(self.root, "CHANGELOG.md"))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn("nothing to release", output)

    def test_status_names_files_missing_from_the_staging_draft(self):
        os.remove(os.path.join(self.gh_state, "assets", STAGING, "b-london.jsonl"))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn(f"not in draft Release {STAGING} with a matching digest: b-london.jsonl",
                      output)

    def test_status_names_staged_files_with_another_digest(self):
        self.stage("b-london.jsonl", FILE_C)
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn(f"not in draft Release {STAGING} with a matching digest: b-london.jsonl",
                      output)

    def test_status_names_pending_files_without_a_staging_draft(self):
        shutil.rmtree(os.path.join(self.gh_state, "assets", STAGING))
        os.remove(os.path.join(self.gh_state, "state.json"))
        code, output = self.release("status")
        self.assertEqual(code, 2)
        self.assertIn(f"not in draft Release {STAGING} with a matching digest: b-london.jsonl",
                      output)

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
        # Only the new file: a-london.jsonl is unchanged since v0.1.0.
        self.assertEqual(os.listdir(assets), ["b-london.jsonl"])
        # The staging draft became 0.2.0; nothing was uploaded again.
        self.assertNotIn(STAGING, self.gh_state_json()["releases"])
        self.assertEqual(self.uploads(), [])
        self.assertEqual([c for c in self.calls() if c[:2] == ["release", "create"]], [])

        manifest = json.loads(self.origin_file("release-0.2.0", "manifest.json"))
        urls = {e["name"]: (e["tag"], e["url"]) for e in manifest["files"]}
        base = "https://github.com/Owner/Repo/releases/download"
        self.assertEqual(urls, {
            "a-london.jsonl": ("v0.1.0", f"{base}/v0.1.0/a-london.jsonl"),
            "b-london.jsonl": ("0.2.0", f"{base}/0.2.0/b-london.jsonl"),
        })
        changelog = self.origin_file("release-0.2.0", "CHANGELOG.md")
        self.assertIn(f"## [Unreleased]\n\n## [0.2.0] - {TODAY}\n\n### Added\n\n- `b-london.jsonl`",
                      changelog)
        self.assertIn("[0.2.0]: https://github.com/Owner/Repo/compare/v0.1.0...0.2.0", changelog)
        self.assertIn("pull request: https://github.com/Owner/Repo/pull/1", output)

    def test_prepare_requires_bump(self):
        code, output = self.release("prepare")
        self.assertEqual(code, 2)
        self.assertIn("--bump", output)

    def test_prepare_refuses_other_changes(self):
        self.write("README.md", "edited\n")
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("other changes", output)
        self.assertNoVersionDraft()

    def test_prepare_stops_when_version_is_published(self):
        with open(os.path.join(self.gh_state, "state.json"), "w") as file:
            json.dump({"releases": {"0.2.0": {"draft": False}}, "prs": []}, file)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("published Release or tag 0.2.0 already exists", output)
        self.assertEqual([c for c in self.calls() if c[:2] == ["release", "edit"]], [])

    def test_prepare_resumes_a_draft(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_PR_FAIL": "1"}):
            code, _ = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertTrue(self.gh_state_json()["releases"]["0.2.0"]["draft"])
        code, output = self.release("prepare")
        self.assertEqual(code, 0, output)
        self.assertIn("resuming draft 0.2.0", output)
        self.assertIn("pull request: https://github.com/Owner/Repo/pull/1", output)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(os.listdir(os.path.join(self.gh_state, "assets", "0.2.0")),
                         ["b-london.jsonl"])

    def test_prepare_deletes_staged_files_that_are_not_pending(self):
        self.stage("stray-london.jsonl", FILE_C)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 0, output)
        self.assertIn("deleted from draft: stray-london.jsonl", output)
        self.assertEqual(os.listdir(os.path.join(self.gh_state, "assets", "0.2.0")),
                         ["b-london.jsonl"])

    def test_prepare_stops_on_a_published_staging_release(self):
        state = self.gh_state_json()
        state["releases"][STAGING]["draft"] = False
        with open(os.path.join(self.gh_state, "state.json"), "w") as file:
            json.dump(state, file)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn(f"Release {STAGING} is published", output)
        self.assertNoVersionDraft()

    def test_status_after_prepare_resumes_with_publish(self):
        self.release("prepare", "--bump", "minor")
        code, output = self.release("status")
        self.assertEqual(code, 0)
        self.assertIn("draft: 0.2.0 (resume)", output)
        self.assertIn("next: publish", output)

    def test_prepare_rewrites_readme_by_region(self):
        self.release("prepare", "--bump", "minor")
        base = "https://github.com/Owner/Repo/releases/download"
        self.assertEqual(self.origin_file("release-0.2.0", "README.md"), textwrap.dedent(f"""\
            # repo

            ## CORDs

            Intro stays.

            ### Las Vegas

            Not published yet.

            ### London

            - [a-london.jsonl]({base}/v0.1.0/a-london.jsonl)
            - [b-london.jsonl]({base}/0.2.0/b-london.jsonl)

            ### Moscow

            Not published yet.

            ## References

            - kept
            """))

    def test_prepare_updates_stats_for_added_and_changed_files(self):
        # a-london.jsonl changed (tag null, listed under Changed); b-london.jsonl is new.
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, None),
                             self.entry("b-london.jsonl", FILE_B, None)])
        # a-london.jsonl is not in senzing-ready/, so its stats come from the draft.
        self.stage("a-london.jsonl", FILE_A)
        self.write("CHANGELOG.md", publish_cord.upload.CHANGELOG_HEADER
                   + "\n## [Unreleased]\n\n### Added\n\n- `b-london.jsonl`\n"
                   + "\n### Changed\n\n- `a-london.jsonl`\n")
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.origin_file("release-0.2.0", "CORD_stats.xlsx"), (
            "stats\n"
            f"--stats CORD_stats.xlsx --reason Added in 0.2.0 --date {TODAY} b-london.jsonl\n"
            f"--stats CORD_stats.xlsx --reason Changed in 0.2.0 --date {TODAY} a-london.jsonl\n"
        ))
        self.assertEqual(sorted(os.listdir(os.path.join(self.gh_state, "assets", "0.2.0"))),
                         ["a-london.jsonl", "b-london.jsonl"])

    def test_prepare_stops_on_unmapped_region(self):
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0"),
                             self.entry("c-paris.jsonl", FILE_B, None)])
        self.stage("c-paris.jsonl", FILE_B)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("region 'paris' has no README heading; add it to REGIONS in "
                      "upload_senzing_ready.py", output)
        self.assertNoVersionDraft()

    def test_prepare_stops_on_name_without_region(self):
        self.write_manifest([self.entry("a-london.jsonl", FILE_A, "v0.1.0"),
                             self.entry("bob.jsonl", FILE_B, None)])
        self.stage("bob.jsonl", FILE_B)
        code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("bob.jsonl: expected <source>-<region>.jsonl", output)
        self.assertNoVersionDraft()

    def test_prepare_stops_without_openpyxl(self):
        with mock.patch.object(publish_cord, "openpyxl_available", lambda: False):
            code, output = self.release("prepare", "--bump", "minor")
        self.assertEqual(code, 2)
        self.assertIn("pip install openpyxl", output)
        self.assertNoVersionDraft()

    # --- publish ---

    def test_publish_merges_publishes_and_verifies(self):
        self.release("prepare", "--bump", "minor")
        code, output = self.release("publish")
        self.assertEqual(code, 0, output)
        self.assertIn(["pr", "merge", "1", "--merge", "--delete-branch"], self.calls())
        merge = self.gh_state_json()["prs"][0]["mergeCommit"]["oid"]
        release = self.gh_state_json()["releases"]["0.2.0"]
        self.assertEqual((release["draft"], release["target"], release["latest"]),
                         (False, merge, True))
        self.assertEqual(output.count("verified: "), 1)
        self.assertIn("verified: https://github.com/Owner/Repo/releases/download/0.2.0/"
                      "b-london.jsonl", output)
        self.assertEqual(self.git("branch", "--show-current"), "main")
        self.assertEqual(self.git("rev-parse", "HEAD"), merge)

    def test_publish_blocked_merge_leaves_draft(self):
        self.release("prepare", "--bump", "minor")
        with mock.patch.dict(os.environ, {"FAKE_GH_MERGE_FAIL": "1"}):
            code, output = self.release("publish")
        self.assertEqual(code, 2)
        self.assertIn("protected branch", output)
        self.assertTrue(self.gh_state_json()["releases"]["0.2.0"]["draft"])

    def test_publish_reports_a_mismatched_download(self):
        self.release("prepare", "--bump", "minor")
        with open(os.path.join(self.gh_state, "assets", "0.2.0", "b-london.jsonl"), "wb") as file:
            file.write(b"tampered\n")
        code, output = self.release("publish")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH: https://github.com/Owner/Repo/releases/download/0.2.0/"
                      "b-london.jsonl", output)

    def test_publish_without_draft_stops(self):
        code, output = self.release("publish")
        self.assertEqual(code, 2)
        self.assertIn("no draft Release", output)


if __name__ == "__main__":
    unittest.main()
