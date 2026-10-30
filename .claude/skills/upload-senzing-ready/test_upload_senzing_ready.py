"""Tests for upload_senzing_ready.py, run against fake gh and curl executables.

Run with:
  python3 -m unittest discover -s .claude/skills/upload-senzing-ready -p 'test_*.py'
"""

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
import upload_senzing_ready  # noqa: E402

STAGING = upload_senzing_ready.STAGING

# A fake gh that keeps Releases in $FAKE_GH_STATE: releases.json records each
# Release's draft flag, and assets/<tag>/ holds its files. Every call is logged.
FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import hashlib, json, os, shutil, sys
    state = os.environ["FAKE_GH_STATE"]
    args = sys.argv[1:]
    with open(os.path.join(state, "calls.log"), "a") as log:
        log.write(json.dumps(args) + "\\n")
    path = os.path.join(state, "releases.json")
    releases = json.load(open(path)) if os.path.exists(path) else {}
    if os.environ.get("FAKE_GH_UNAUTH"):
        sys.exit("HTTP 401: Bad credentials (https://api.github.com/graphql)")
    def assets(tag):
        directory = os.path.join(state, "assets", tag)
        os.makedirs(directory, exist_ok=True)
        return directory
    if args[:2] == ["release", "view"]:
        tag = args[2]
        if tag not in releases:
            sys.exit("release not found")
        d = assets(tag)
        found = []
        for n in sorted(os.listdir(d)):
            data = open(os.path.join(d, n), "rb").read()
            found.append({"name": n, "size": len(data),
                          "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
        print(json.dumps({"isDraft": releases[tag]["draft"], "assets": found}))
    elif args[:2] == ["release", "create"]:
        releases[args[2]] = {"draft": "--draft" in args}
        json.dump(releases, open(path, "w"))
    elif args[:2] == ["release", "upload"]:
        source = args[3]
        if os.path.basename(source) in os.environ.get("FAKE_GH_FAIL", "").split(","):
            sys.exit("HTTP 502: upload failed")
        shutil.copy(source, assets(args[2]))
    elif args[:2] == ["release", "delete-asset"]:
        os.remove(os.path.join(assets(args[2]), args[3]))
    else:
        sys.exit("fake gh: unsupported " + " ".join(args))
    """
)

# A fake curl standing in for senzing.com: it serves $FAKE_GH_STATE/senzing/<name>,
# answers a HEAD request (-sSIL) with a redirect and then the file's status and
# Content-Length, and logs every call to curl.log. FAKE_CURL_STATUS forces the
# HEAD status, FAKE_CURL_NO_LENGTH drops Content-Length, FAKE_CURL_DOWN fails
# every request and FAKE_CURL_GET_FAIL fails the download.
FAKE_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    state = os.environ["FAKE_GH_STATE"]
    args = sys.argv[1:]
    with open(os.path.join(state, "curl.log"), "a") as log:
        log.write(json.dumps(args) + "\\n")
    if os.environ.get("FAKE_CURL_DOWN"):
        sys.exit("curl: (6) Could not resolve host: senzing.com")
    url = args[-1]
    path = os.path.join(state, "senzing", url.rsplit("/", 1)[1])
    if "-sSIL" in args:
        print("HTTP/1.1 301 Moved Permanently\\r\\ncontent-length: 0\\r\\n\\r")
        status = os.environ.get("FAKE_CURL_STATUS") or ("200" if os.path.exists(path) else "404")
        print(f"HTTP/2 {status} \\r")
        if status == "200" and not os.environ.get("FAKE_CURL_NO_LENGTH"):
            print(f"content-length: {os.path.getsize(path)}\\r")
        print("\\r")
    elif os.environ.get("FAKE_CURL_GET_FAIL"):
        sys.exit("curl: (56) Recv failure: Connection reset by peer")
    elif not os.path.exists(path):
        sys.exit("curl: (22) The requested URL returned error: 404")
    else:
        shutil.copy(path, args[args.index("-o") + 1])
    """
)

LINES_A = b'{"RECORD_ID": "1"}\n{"RECORD_ID": "2"}\n'
LINES_B = b'{"RECORD_ID": "1"}\n{"RECORD_ID": "3"}\n'


def read(path):
    with open(path) as file:
        return file.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeToolsTest(unittest.TestCase):
    """Fake gh and curl on PATH, a repository root and the fakes' state."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "repo")
        self.state = os.path.join(self.tmp, "gh")
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.root, self.state, bin_dir, os.path.join(self.root, "senzing-ready")):
            os.makedirs(directory, exist_ok=True)
        for name, body in (("gh", FAKE_GH), ("curl", FAKE_CURL)):
            exe = os.path.join(bin_dir, name)
            with open(exe, "w") as file:
                file.write(body)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        os.makedirs(os.path.join(self.state, "senzing"))
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(
            ["git", "-C", self.root, "remote", "add", "origin", "git@github.com:Owner/Repo.git"],
            check=True,
        )
        env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "FAKE_GH_STATE": self.state,
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers ---

    def write_jsonl(self, name, data):
        with open(os.path.join(self.root, "senzing-ready", name), "wb") as file:
            file.write(data)

    def write_manifest(self, *entries):
        with open(os.path.join(self.root, "manifest.json"), "w") as file:
            json.dump({"files": list(entries)}, file, indent=2)

    def manifest(self):
        with open(os.path.join(self.root, "manifest.json")) as file:
            return {entry["name"]: entry for entry in json.load(file)["files"]}

    def changelog(self):
        path = os.path.join(self.root, "CHANGELOG.md")
        return read(path) if os.path.exists(path) else None

    def calls(self):
        log = os.path.join(self.state, "calls.log")
        if not os.path.exists(log):
            return []
        return [json.loads(line) for line in read(log).splitlines()]

    def curls(self):
        log = os.path.join(self.state, "curl.log")
        if not os.path.exists(log):
            return []
        return [json.loads(line) for line in read(log).splitlines()]

    def downloads(self):
        return [call for call in self.curls() if "-o" in call]

    def serve(self, name, data):
        """Put a file on the fake senzing.com."""
        with open(os.path.join(self.state, "senzing", name), "wb") as file:
            file.write(data)

    def uploads(self):
        return [call for call in self.calls() if call[:2] == ["release", "upload"]]

    def releases(self):
        path = os.path.join(self.state, "releases.json")
        return json.loads(read(path)) if os.path.exists(path) else {}

    def staged(self):
        directory = os.path.join(self.state, "assets", STAGING)
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def stage(self, name, data, draft=True):
        """Put a file on the staging Release as if it had been uploaded earlier."""
        releases = {**self.releases(), STAGING: {"draft": draft}}
        with open(os.path.join(self.state, "releases.json"), "w") as file:
            json.dump(releases, file)
        directory = os.path.join(self.state, "assets", STAGING)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, name), "wb") as file:
            file.write(data)

    def entry(self, name, data, **extra):
        return {
            "name": name, "size": len(data), "sha256": sha(data), "tag": "0.1.0",
            "url": f"https://example.com/0.1.0/{name}", **extra,
        }

    def run_upload(self, *args):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            code = upload_senzing_ready.main(["--root", self.root, *args])
        return code, out.getvalue()


class UploadTest(FakeToolsTest):

    def test_old_jsonl_dir_alone_stops(self):
        os.rmdir(os.path.join(self.root, "senzing-ready"))
        os.makedirs(os.path.join(self.root, "jsonl"))
        code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn("mv jsonl senzing-ready", output)

    def test_new_file_is_uploaded_and_added(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(
            self.manifest()["a-london.jsonl"],
            {
                "name": "a-london.jsonl", "size": len(LINES_A), "sha256": sha(LINES_A),
                "tag": None, "url": None, "source": None,
            },
        )
        self.assertIn("## [Unreleased]\n\n### Added\n\n- `a-london.jsonl`", self.changelog())
        self.assertTrue(self.changelog().startswith("# Changelog\n"))

    def test_first_upload_creates_the_staging_draft(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        self.assertEqual(self.releases(), {STAGING: {"draft": True}})
        self.assertEqual(self.staged(), ["a-london.jsonl"])
        upload = self.uploads()[0]
        self.assertEqual(upload[:3], ["release", "upload", STAGING])
        self.assertIn("--clobber", upload)
        self.assertEqual(upload[upload.index("--repo") + 1], "Owner/Repo")

    def test_upload_reuses_an_existing_staging_draft(self):
        self.stage("b-london.jsonl", LINES_B)
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        self.assertEqual([c for c in self.calls() if c[:2] == ["release", "create"]], [])
        self.assertEqual(self.staged(), ["a-london.jsonl", "b-london.jsonl"])

    def test_published_staging_release_stops(self):
        self.stage("b-london.jsonl", LINES_B, draft=False)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn(f"Release {STAGING} is published", output)
        self.assertEqual(self.uploads(), [])

    def test_staging_git_tag_stops(self):
        git = ["git", "-C", self.root, "-c", "user.name=t", "-c", "user.email=t@t",
               "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false"]
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        subprocess.run([*git, "tag", STAGING], check=True)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn(f"a git tag named {STAGING} exists", output)
        self.assertEqual(self.calls(), [])

    def test_changed_file_is_uploaded_and_unreleased(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A, source="https://example.com/a"))
        self.write_jsonl("a-london.jsonl", LINES_B)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        entry = self.manifest()["a-london.jsonl"]
        self.assertEqual((entry["sha256"], entry["size"]), (sha(LINES_B), len(LINES_B)))
        self.assertEqual((entry["tag"], entry["url"]), (None, None))
        self.assertIsNone(entry["source"])
        self.assertIn("### Changed\n\n- `a-london.jsonl`", self.changelog())

    def test_released_file_is_current(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A))
        self.write_jsonl("a-london.jsonl", LINES_A)
        before = read(os.path.join(self.root, "manifest.json"))
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(read(os.path.join(self.root, "manifest.json")), before)
        self.assertIn("current: a-london.jsonl", output)

    def test_staged_file_with_matching_digest_is_current(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A, tag=None, url=None))
        self.stage("a-london.jsonl", LINES_A)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(self.uploads(), [])
        self.assertIn("current: a-london.jsonl", output)

    def test_missing_staged_asset_is_repaired_without_changelog(self):
        old = self.entry("a-london.jsonl", LINES_A, tag=None, url=None)
        self.write_manifest(old)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.uploads()), 1)
        self.assertIn("uploaded: a-london.jsonl (repair)", output)
        self.assertEqual(self.manifest()["a-london.jsonl"], old)
        self.assertIsNone(self.changelog())

    def test_staged_asset_with_another_digest_is_replaced(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A, tag=None, url=None))
        self.stage("a-london.jsonl", LINES_B)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        with open(os.path.join(self.state, "assets", STAGING, "a-london.jsonl"), "rb") as file:
            self.assertEqual(file.read(), LINES_A)

    def test_entry_without_local_file_is_untouched(self):
        other = self.entry("b-london.jsonl", LINES_B)
        self.write_manifest(other)
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        self.assertEqual(self.manifest()["b-london.jsonl"], other)

    def test_remove_released_file_touches_manifest_only(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A), self.entry("b-london.jsonl", LINES_B))
        code, _ = self.run_upload("--remove", "b-london.jsonl")
        self.assertEqual(code, 0)
        self.assertEqual(list(self.manifest()), ["a-london.jsonl"])
        self.assertIn("### Removed\n\n- `b-london.jsonl`\n", self.changelog())
        self.assertEqual([c for c in self.calls() if c[:2] == ["release", "delete-asset"]], [])

    def test_remove_staged_file_deletes_its_asset(self):
        self.write_manifest(self.entry("b-london.jsonl", LINES_B, tag=None, url=None))
        self.stage("b-london.jsonl", LINES_B)
        code, output = self.run_upload("--remove", "b-london.jsonl")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.manifest(), {})
        self.assertEqual(self.staged(), [])

    def test_remove_unknown_name_stops(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A))
        self.write_jsonl("c-london.jsonl", LINES_A)
        code, output = self.run_upload("--remove", "nope.jsonl")
        self.assertEqual(code, 2)
        self.assertIn("nope.jsonl", output)
        self.assertEqual(self.uploads(), [])

    def test_second_run_uploads_nothing(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        self.run_upload()
        self.assertEqual(len(self.uploads()), 1)

    def test_changelog_keeps_one_line_per_file(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A))
        self.write_jsonl("a-london.jsonl", LINES_B)
        self.run_upload()
        self.write_jsonl("a-london.jsonl", LINES_B + b'{"RECORD_ID": "4"}\n')
        self.run_upload()
        self.assertEqual(self.changelog().count("- `a-london.jsonl`"), 1)

    def test_added_stays_added_when_changed_again(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        self.write_jsonl("a-london.jsonl", LINES_B)
        self.run_upload()
        changelog = self.changelog()
        self.assertEqual(changelog.count("- `a-london.jsonl`"), 1)
        self.assertIn(f"### Added\n\n- `a-london.jsonl` ({len(LINES_B):,} bytes, SHA-256 `{sha(LINES_B)}`)", changelog)
        self.assertNotIn("### Changed", changelog)

    def test_unreleased_goes_above_existing_release(self):
        with open(os.path.join(self.root, "CHANGELOG.md"), "w") as file:
            file.write(
                upload_senzing_ready.CHANGELOG_HEADER
                + "\n## [0.1.0] - 2026-10-27\n\n### Added\n\n- `old.jsonl`\n\n"
                "[0.1.0]: https://example.com/0.1.0\n"
            )
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.run_upload()
        changelog = self.changelog()
        self.assertLess(changelog.index("## [Unreleased]"), changelog.index("## [0.1.0]"))
        self.assertTrue(changelog.endswith("[0.1.0]: https://example.com/0.1.0\n"))

    def test_invalid_files_are_refused(self):
        self.write_jsonl("Upper.jsonl", LINES_A)
        self.write_jsonl("bad-london.jsonl", b'{"RECORD_ID": "1"}\nnot json\n')
        self.write_jsonl("blank-london.jsonl", b'{"RECORD_ID": "1"}\n\n{"RECORD_ID": "2"}\n')
        self.write_jsonl("good-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertEqual(list(self.manifest()), ["good-london.jsonl"])
        self.assertIn("refused: Upper.jsonl: name must match", output)
        self.assertIn("refused: bad-london.jsonl: line 2 is not valid JSON", output)
        self.assertIn("refused: blank-london.jsonl: line 2 is not valid JSON", output)

    def test_names_without_a_known_region_are_refused(self):
        self.write_jsonl("bob.jsonl", LINES_A)
        self.write_jsonl("x-paris.jsonl", LINES_A)
        self.write_jsonl("open-sanctions-london.jsonl", LINES_B)
        code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertEqual(list(self.manifest()), ["open-sanctions-london.jsonl"])
        self.assertIn("refused: bob.jsonl: expected <source>-<region>.jsonl", output)
        self.assertIn("refused: x-paris.jsonl: region 'paris' has no catalog heading; "
                      "add it to REGIONS in upload_senzing_ready.py", output)
        self.assertEqual(len(self.uploads()), 1)

    def test_region_that_shares_a_heading_is_accepted(self):
        self.write_jsonl("globaldata-london_central_a.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertEqual(list(self.manifest()), ["globaldata-london_central_a.jsonl"])
        self.assertEqual(len(self.uploads()), 1)
        self.assertNotIn("refused", output)

    def test_oversized_file_is_refused(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        with mock.patch.object(upload_senzing_ready, "MAX_SIZE", 10):
            code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertIn("over the 2 GiB limit", output)
        self.assertEqual(self.uploads(), [])

    def test_non_jsonl_files_are_ignored(self):
        self.write_jsonl("notes.txt", b"hello")
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))
        self.assertIn("no .jsonl files", output)

    def test_unauthenticated_stops_before_changes(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        with mock.patch.dict(os.environ, {"FAKE_GH_UNAUTH": "1"}):
            code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn("gh auth login", output)
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))

    def test_failed_upload_leaves_entry_unchanged(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A))
        self.write_jsonl("a-london.jsonl", LINES_B)
        self.write_jsonl("b-london.jsonl", LINES_A)
        with mock.patch.dict(os.environ, {"FAKE_GH_FAIL": "a-london.jsonl"}):
            code, output = self.run_upload()
        self.assertEqual(code, 1)
        manifest = self.manifest()
        self.assertEqual(manifest["a-london.jsonl"]["sha256"], sha(LINES_A))
        self.assertIn("b-london.jsonl", manifest)
        self.assertIn("failed: a-london.jsonl: HTTP 502: upload failed", output)
        self.assertNotIn("a-london.jsonl", self.changelog())

    def test_dry_run_changes_nothing(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would upload: a-london.jsonl (added, no matching senzing.com copy)", output)
        self.assertEqual(self.uploads(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))

    def test_manifest_is_sorted_by_name(self):
        self.write_manifest(self.entry("m-london.jsonl", LINES_B))
        self.write_jsonl("z-london.jsonl", LINES_A)
        self.write_jsonl("a-london.jsonl", LINES_B)
        self.run_upload()
        with open(os.path.join(self.root, "manifest.json")) as file:
            names = [entry["name"] for entry in json.load(file)["files"]]
        self.assertEqual(names, ["a-london.jsonl", "m-london.jsonl", "z-london.jsonl"])


SENZING = "https://senzing.com/datasets/"


class SenzingSourceTest(FakeToolsTest):
    """The senzing.com check that sets an added or changed entry's source."""

    def test_matching_senzing_copy_sets_source(self):
        self.serve("a-london.jsonl", LINES_A)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.manifest()["a-london.jsonl"]["source"], SENZING + "a-london.jsonl")
        self.assertIn("uploaded: a-london.jsonl (added, senzing.com copy matches)", output)
        self.assertEqual(self.curls()[0], ["-sSIL", SENZING + "a-london.jsonl"])
        self.assertEqual(len(self.downloads()), 1)

    def test_404_sets_null_source_without_download(self):
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertIsNone(self.manifest()["a-london.jsonl"]["source"])
        self.assertIn("uploaded: a-london.jsonl (added, no matching senzing.com copy)", output)
        self.assertEqual(self.downloads(), [])
        self.assertEqual(self.staged(), ["a-london.jsonl"])

    def test_different_size_sets_null_source_without_download(self):
        self.serve("a-london.jsonl", LINES_A + b'{"RECORD_ID": "9"}\n')
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertIsNone(self.manifest()["a-london.jsonl"]["source"])
        self.assertEqual(len(self.curls()), 1)
        self.assertEqual(self.downloads(), [])
        self.assertEqual(self.staged(), ["a-london.jsonl"])

    def test_same_size_different_sha256_sets_null_source(self):
        self.assertEqual(len(LINES_A), len(LINES_B))
        self.serve("a-london.jsonl", LINES_B)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertIsNone(self.manifest()["a-london.jsonl"]["source"])
        self.assertIn("uploaded: a-london.jsonl (added, no matching senzing.com copy)", output)
        self.assertEqual(len(self.downloads()), 1)
        self.assertEqual(self.staged(), ["a-london.jsonl"])

    def test_changed_file_gets_source_rechecked(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A, source=SENZING + "a-london.jsonl"),
                            self.entry("b-london.jsonl", LINES_A))
        self.serve("a-london.jsonl", LINES_A)  # senzing.com still has the old copy
        self.serve("b-london.jsonl", LINES_B)
        self.write_jsonl("a-london.jsonl", LINES_B)
        self.write_jsonl("b-london.jsonl", LINES_B)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        manifest = self.manifest()
        self.assertIsNone(manifest["a-london.jsonl"]["source"])
        self.assertEqual(manifest["b-london.jsonl"]["source"], SENZING + "b-london.jsonl")
        self.assertIn("uploaded: a-london.jsonl (changed, no matching senzing.com copy)", output)
        self.assertIn("uploaded: b-london.jsonl (changed, senzing.com copy matches)", output)

    def test_repaired_file_keeps_source_and_is_not_checked(self):
        old = self.entry("a-london.jsonl", LINES_A, tag=None, url=None,
                         source=SENZING + "a-london.jsonl")
        self.write_manifest(old)
        self.write_jsonl("a-london.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.manifest()["a-london.jsonl"], old)
        self.assertIn("uploaded: a-london.jsonl (repair)\n", output)
        self.assertEqual(self.curls(), [])

    def test_current_and_refused_files_are_not_checked(self):
        self.write_manifest(self.entry("a-london.jsonl", LINES_A))
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.write_jsonl("bad-london.jsonl", b"not json\n")
        self.write_jsonl("x-paris.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertIn("current: a-london.jsonl", output)
        self.assertEqual(self.curls(), [])

    def test_dry_run_runs_the_same_check(self):
        self.serve("a-london.jsonl", LINES_A)
        self.write_jsonl("a-london.jsonl", LINES_A)
        self.write_jsonl("b-london.jsonl", LINES_B)
        code, output = self.run_upload("--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("would upload: a-london.jsonl (added, senzing.com copy matches)", output)
        self.assertIn("would upload: b-london.jsonl (added, no matching senzing.com copy)", output)
        self.assertEqual(len(self.downloads()), 1)
        self.assertEqual(self.uploads(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))

    def test_download_goes_to_a_temporary_directory_that_is_removed(self):
        scratch = os.path.join(self.tmp, "scratch")
        os.makedirs(scratch)
        self.serve("a-london.jsonl", LINES_A)
        self.write_jsonl("a-london.jsonl", LINES_A)
        with mock.patch.object(tempfile, "tempdir", scratch):
            code, output = self.run_upload()
        self.assertEqual(code, 0, output)
        target = self.downloads()[0]
        target = target[target.index("-o") + 1]
        self.assertEqual(os.path.dirname(os.path.dirname(target)), scratch)
        self.assertEqual(os.listdir(scratch), [])

    def test_other_failures_stop_before_any_upload(self):
        self.serve("a-london.jsonl", LINES_A)
        cases = [
            ({"FAKE_CURL_STATUS": "403"}, "HTTP 403"),
            ({"FAKE_CURL_STATUS": "503"}, "HTTP 503"),
            ({"FAKE_CURL_DOWN": "1"}, "Could not resolve host"),
            ({"FAKE_CURL_NO_LENGTH": "1"}, "no Content-Length"),
            ({"FAKE_CURL_GET_FAIL": "1"}, "cannot download " + SENZING + "a-london.jsonl"),
        ]
        for dry_run in ([], ["--dry-run"]):
            for env, message in cases:
                with self.subTest(env=env, dry_run=dry_run):
                    self.write_jsonl("a-london.jsonl", LINES_A)
                    self.write_jsonl("b-london.jsonl", LINES_B)
                    with mock.patch.dict(os.environ, env):
                        code, output = self.run_upload(*dry_run)
                    self.assertEqual(code, 2, output)
                    self.assertIn("stopped, nothing changed: ", output)
                    self.assertIn(message, output)
                    self.assertEqual(self.uploads(), [])
                    self.assertEqual(self.releases(), {})
                    self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))
                    self.assertIsNone(self.changelog())


if __name__ == "__main__":
    unittest.main()
