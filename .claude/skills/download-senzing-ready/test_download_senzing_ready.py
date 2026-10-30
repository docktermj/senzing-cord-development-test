"""Tests for download_senzing_ready.py, run against fake curl and gh executables.

Run with:
  python3 -m unittest discover -s .claude/skills/download-senzing-ready -p 'test_*.py'
"""

import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import download_senzing_ready  # noqa: E402

# Both fakes serve files from $FAKE_STORE: curl by the URL's last path segment
# from source/ for a senzing.com /datasets/ URL and from release/ otherwise, gh
# by the --pattern name from staging/. gh release view answers from
# $FAKE_STORE/releases.json ({tag: {"isDraft": ..., "assets": [...]}}). Every
# call is logged to $FAKE_STORE/calls.log.
FAKE_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    store = os.environ["FAKE_STORE"]
    args = sys.argv[1:]
    with open(os.path.join(store, "calls.log"), "a") as log:
        log.write(json.dumps(["curl", *args]) + "\\n")
    folder = "source" if "/datasets/" in args[-1] else "release"
    source = os.path.join(store, folder, args[-1].rsplit("/", 1)[1])
    if not os.path.exists(source):
        sys.exit("curl: (22) The requested URL returned error: 404")
    shutil.copy(source, args[args.index("-o") + 1])
    """
)

FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    store = os.environ["FAKE_STORE"]
    args = sys.argv[1:]
    with open(os.path.join(store, "calls.log"), "a") as log:
        log.write(json.dumps(["gh", *args]) + "\\n")
    if os.environ.get("FAKE_GH_UNAUTH"):
        sys.exit("HTTP 401: Bad credentials (https://api.github.com/graphql)")
    if args[:2] == ["release", "view"]:
        releases = os.path.join(store, "releases.json")
        release = json.load(open(releases)).get(args[2]) if os.path.exists(releases) else None
        if release is None:
            sys.exit("release not found")
        print(json.dumps(release))
        sys.exit(0)
    source = os.path.join(store, args[2], args[args.index("--pattern") + 1])
    if not os.path.exists(source):
        sys.exit("release not found")
    shutil.copy(source, args[args.index("--output") + 1])
    """
)

FILE_A = b'{"RECORD_ID": "1"}\n'
FILE_B = b'{"RECORD_ID": "2"}\n{"RECORD_ID": "3"}\n'
EDITED = b'{"RECORD_ID": "edited"}\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeToolsTest(unittest.TestCase):
    """Fake curl and gh on PATH, a repository root and the fakes' store."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "repo")
        self.store = os.path.join(self.tmp, "store")
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.root, os.path.join(self.store, "release"),
                          os.path.join(self.store, "unreleased"),
                          os.path.join(self.store, "source"), bin_dir):
            os.makedirs(directory)
        for name, body in (("curl", FAKE_CURL), ("gh", FAKE_GH)):
            exe = os.path.join(bin_dir, name)
            with open(exe, "w") as file:
                file.write(body)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        patcher = mock.patch.dict(os.environ, {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "FAKE_STORE": self.store,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

        # a.jsonl is released; b.jsonl is staged on the unreleased draft.
        self.publish("a.jsonl", FILE_A)
        self.stage("b.jsonl", FILE_B)
        self.write_manifest([
            self.entry("a.jsonl", FILE_A, "https://example.com/releases/download/0.1.1/a.jsonl"),
            self.entry("b.jsonl", FILE_B, None),
        ])

    # --- helpers ---

    def entry(self, name, data, url):
        return {"name": name, "size": len(data), "sha256": sha(data),
                "tag": url and "0.1.1", "url": url}

    def write_manifest(self, entries):
        with open(os.path.join(self.root, "manifest.json"), "w") as file:
            json.dump({"files": entries}, file)

    def publish(self, name, data):
        with open(os.path.join(self.store, "release", name), "wb") as file:
            file.write(data)

    def stage(self, name, data):
        with open(os.path.join(self.store, "unreleased", name), "wb") as file:
            file.write(data)

    def local(self, name):
        path = os.path.join(self.root, "senzing-ready", name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as file:
            return file.read()

    def write_local(self, name, data):
        os.makedirs(os.path.join(self.root, "senzing-ready"), exist_ok=True)
        with open(os.path.join(self.root, "senzing-ready", name), "wb") as file:
            file.write(data)

    def calls(self):
        log = os.path.join(self.store, "calls.log")
        if not os.path.exists(log):
            return []
        with open(log) as file:
            return [json.loads(line) for line in file]

    def download(self, *args):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            code = download_senzing_ready.main(["--root", self.root, *args])
        return code, out.getvalue()


class DownloadTest(FakeToolsTest):

    def test_old_jsonl_dir_alone_stops(self):
        os.makedirs(os.path.join(self.root, "jsonl"))
        code, output = self.download()
        self.assertEqual(code, 2)
        self.assertIn("mv jsonl senzing-ready", output)
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "senzing-ready")))

    def test_old_jsonl_dir_is_ignored_when_senzing_ready_exists(self):
        os.makedirs(os.path.join(self.root, "jsonl"))
        os.makedirs(os.path.join(self.root, "senzing-ready"))
        code, _ = self.download()
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(os.path.join(self.root, "jsonl")), [])

    def test_downloads_every_entry_into_new_ready_dir(self):
        code, output = self.download()
        self.assertEqual(code, 0, output)
        self.assertEqual((self.local("a.jsonl"), self.local("b.jsonl")), (FILE_A, FILE_B))
        self.assertIn("downloaded: a.jsonl", output)
        self.assertIn("downloaded: b.jsonl", output)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "senzing-ready"))),
                         ["a.jsonl", "b.jsonl"])

    def test_released_entry_uses_url_and_unreleased_uses_staging_draft(self):
        self.download()
        tools = {call[0] for call in self.calls()}
        self.assertEqual(tools, {"curl", "gh"})
        gh = [call for call in self.calls() if call[0] == "gh"][0]
        self.assertEqual(gh[1:6], ["release", "download", "unreleased", "--pattern", "b.jsonl"])
        curl = [call for call in self.calls() if call[0] == "curl"][0]
        self.assertEqual(curl[1], "-fsSL")
        self.assertEqual(curl[-1], "https://example.com/releases/download/0.1.1/a.jsonl")

    def test_current_files_are_not_downloaded_again(self):
        self.download()
        code, output = self.download()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("current: a.jsonl", output)
        self.assertIn("current: b.jsonl", output)

    def test_locally_modified_file_is_kept(self):
        self.write_local("a.jsonl", EDITED)
        code, output = self.download()
        self.assertEqual(code, 1)
        self.assertEqual(self.local("a.jsonl"), EDITED)
        self.assertIn("locally modified: a.jsonl (kept; --force a.jsonl overwrites it)", output)
        self.assertEqual(self.local("b.jsonl"), FILE_B)

    def test_force_overwrites_only_the_named_file(self):
        self.write_local("a.jsonl", EDITED)
        self.write_local("b.jsonl", EDITED)
        code, _ = self.download("--force", "a.jsonl")
        self.assertEqual(code, 1)
        self.assertEqual(self.local("a.jsonl"), FILE_A)
        self.assertEqual(self.local("b.jsonl"), EDITED)
        code, _ = self.download("--force", "a.jsonl", "--force", "b.jsonl")
        self.assertEqual(code, 0)
        self.assertEqual(self.local("b.jsonl"), FILE_B)

    def test_force_unknown_name_stops(self):
        code, output = self.download("--force", "nope.jsonl")
        self.assertEqual(code, 2)
        self.assertIn("nope.jsonl", output)
        self.assertEqual(self.calls(), [])

    def test_missing_manifest_stops(self):
        os.remove(os.path.join(self.root, "manifest.json"))
        code, output = self.download()
        self.assertEqual(code, 2)
        self.assertIn("manifest.json not found", output)

    def test_mismatch_fails_that_file_and_leaves_nothing_behind(self):
        self.publish("a.jsonl", b"tampered\n")
        code, output = self.download()
        self.assertEqual(code, 1)
        self.assertIn("failed: a.jsonl: SHA-256 does not match manifest.json", output)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "senzing-ready"))), ["b.jsonl"])

    def test_failed_download_leaves_no_partial_file(self):
        os.remove(os.path.join(self.store, "release", "a.jsonl"))
        code, output = self.download()
        self.assertEqual(code, 1)
        self.assertIn("failed: a.jsonl: curl: (22)", output)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "senzing-ready"))), ["b.jsonl"])

    def test_gh_login_failure_fails_only_unreleased_entries(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_UNAUTH": "1"}):
            code, output = self.download()
        self.assertEqual(code, 1)
        self.assertEqual(self.local("a.jsonl"), FILE_A)
        self.assertIsNone(self.local("b.jsonl"))
        self.assertIn("failed: b.jsonl: HTTP 401: Bad credentials", output)
        self.assertIn("gh auth login", output)

    def test_files_not_in_manifest_are_left_alone(self):
        self.write_local("notes.jsonl", b'{"x": 1}\n')
        self.download()
        self.assertEqual(self.local("notes.jsonl"), b'{"x": 1}\n')


REPO_URL = "https://github.com/owner/repo/releases/download"


class VerifyTest(FakeToolsTest):
    """--verify: Release asset digests through gh, source copies through curl."""

    def setUp(self):
        super().setUp()
        # a.jsonl (0.1.1) has a senzing.com source; b.jsonl (0.1.2) has none;
        # c.jsonl is unreleased.
        self.releases = {
            "0.1.1": {"isDraft": False, "assets": [
                {"name": "a.jsonl", "digest": "sha256:" + sha(FILE_A)}]},
            "0.1.2": {"isDraft": False, "assets": [
                {"name": "b.jsonl", "digest": "sha256:" + sha(FILE_B)}]},
        }
        self.write_releases()
        self.serve_source("a.jsonl", FILE_A)
        self.entries = [
            dict(self.entry("a.jsonl", FILE_A, f"{REPO_URL}/0.1.1/a.jsonl"),
                 source="https://senzing.com/datasets/a.jsonl"),
            dict(self.entry("b.jsonl", FILE_B, f"{REPO_URL}/0.1.2/b.jsonl"), tag="0.1.2",
                 source=None),
            self.entry("c.jsonl", EDITED, None),
        ]
        self.write_manifest(self.entries)

    def write_releases(self):
        with open(os.path.join(self.store, "releases.json"), "w") as file:
            json.dump(self.releases, file)

    def serve_source(self, name, data):
        with open(os.path.join(self.store, "source", name), "wb") as file:
            file.write(data)

    def verify(self):
        return self.download("--verify")

    # --- tests ---

    def test_everything_matches(self):
        code, output = self.verify()
        self.assertEqual(code, 0, output)
        self.assertIn(f"verified: a.jsonl: {REPO_URL}/0.1.1/a.jsonl", output)
        self.assertIn("verified: a.jsonl: https://senzing.com/datasets/a.jsonl", output)
        self.assertIn(f"verified: b.jsonl: {REPO_URL}/0.1.2/b.jsonl", output)
        self.assertIn("unreleased, not checked: c.jsonl", output)
        self.assertNotIn("failed", output)

    def test_writes_nothing_and_downloads_no_release_asset(self):
        self.verify()
        self.assertFalse(os.path.exists(os.path.join(self.root, "senzing-ready")))
        self.assertEqual(os.listdir(self.root), ["manifest.json"])
        curls = [call[-1] for call in self.calls() if call[0] == "curl"]
        self.assertEqual(curls, ["https://senzing.com/datasets/a.jsonl"])
        gh = [call[1:] for call in self.calls() if call[0] == "gh"]
        self.assertEqual(gh, [
            ["release", "view", "0.1.1", "--repo", "owner/repo", "--json", "isDraft,assets"],
            ["release", "view", "0.1.2", "--repo", "owner/repo", "--json", "isDraft,assets"],
        ])

    def test_one_gh_call_per_release(self):
        self.releases["0.1.1"]["assets"].append(
            {"name": "b.jsonl", "digest": "sha256:" + sha(FILE_B)})
        self.write_releases()
        self.entries[1].update(tag="0.1.1", url=f"{REPO_URL}/0.1.1/b.jsonl")
        self.write_manifest(self.entries)
        code, output = self.verify()
        self.assertEqual(code, 0, output)
        self.assertEqual(len([call for call in self.calls() if call[0] == "gh"]), 1)

    def test_digest_mismatch_fails_naming_file_and_url(self):
        tampered = sha(b"tampered\n")
        self.releases["0.1.2"]["assets"][0]["digest"] = "sha256:" + tampered
        self.write_releases()
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn(f"failed: b.jsonl: {REPO_URL}/0.1.2/b.jsonl: asset digest sha256:"
                      f"{tampered} does not match manifest.json", output)
        self.assertIn("verified: a.jsonl", output)

    def test_missing_asset_fails(self):
        self.releases["0.1.2"]["assets"] = []
        self.write_releases()
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn(f"failed: b.jsonl: {REPO_URL}/0.1.2/b.jsonl: no such asset on Release "
                      "0.1.2", output)

    def test_asset_without_digest_fails(self):
        self.releases["0.1.2"]["assets"][0]["digest"] = None
        self.write_releases()
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn(f"failed: b.jsonl: {REPO_URL}/0.1.2/b.jsonl: the asset has no digest",
                      output)

    def test_missing_release_fails(self):
        del self.releases["0.1.2"]
        self.write_releases()
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn(f"failed: b.jsonl: {REPO_URL}/0.1.2/b.jsonl: Release 0.1.2 not found",
                      output)

    def test_draft_release_fails(self):
        self.releases["0.1.2"]["isDraft"] = True
        self.write_releases()
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("failed: b.jsonl: ", output)
        self.assertIn("Release 0.1.2 is a draft", output)

    def test_gh_failure_fails_every_released_entry(self):
        with mock.patch.dict(os.environ, {"FAKE_GH_UNAUTH": "1"}):
            code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn(f"failed: a.jsonl: {REPO_URL}/0.1.1/a.jsonl: cannot read Release 0.1.1: "
                      "HTTP 401", output)
        self.assertIn(f"failed: b.jsonl: {REPO_URL}/0.1.2/b.jsonl: cannot read Release", output)
        # The senzing.com copy is still checked.
        self.assertIn("verified: a.jsonl: https://senzing.com/datasets/a.jsonl", output)

    def test_source_mismatch_fails_naming_file_and_source(self):
        self.serve_source("a.jsonl", b"changed upstream\n")
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("failed: a.jsonl: https://senzing.com/datasets/a.jsonl: "
                      "SHA-256 does not match manifest.json", output)
        self.assertIn(f"verified: a.jsonl: {REPO_URL}/0.1.1/a.jsonl", output)

    def test_source_404_fails(self):
        os.remove(os.path.join(self.store, "source", "a.jsonl"))
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("failed: a.jsonl: https://senzing.com/datasets/a.jsonl: curl: (22)",
                      output)

    def test_null_source_is_checked_only_against_release(self):
        code, output = self.verify()
        self.assertEqual(code, 0, output)
        self.assertNotIn("b.jsonl", " ".join(
            call[-1] for call in self.calls() if call[0] == "curl"))
        self.assertEqual(output.count("verified: b.jsonl"), 1)

    def test_non_release_url_fails(self):
        self.entries[1]["url"] = "https://example.com/b.jsonl"
        self.write_manifest(self.entries)
        code, output = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("failed: b.jsonl: https://example.com/b.jsonl: not a GitHub Release "
                      "download URL", output)

    def test_ignores_local_files_and_old_jsonl_dir(self):
        os.makedirs(os.path.join(self.root, "jsonl"))
        code, output = self.verify()
        self.assertEqual(code, 0, output)
        self.assertFalse(os.path.exists(os.path.join(self.root, "senzing-ready")))

    def test_missing_manifest_stops(self):
        os.remove(os.path.join(self.root, "manifest.json"))
        code, output = self.verify()
        self.assertEqual(code, 2)
        self.assertIn("stopped, nothing checked: manifest.json not found", output)

    def test_force_with_verify_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            self.download("--verify", "--force", "a.jsonl")
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
