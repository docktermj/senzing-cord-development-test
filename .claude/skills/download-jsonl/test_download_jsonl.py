"""Tests for download_jsonl.py, run against fake curl and oras executables.

Run with:
  python3 -m unittest discover -s .claude/skills/download-jsonl -p 'test_*.py'
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
import download_jsonl  # noqa: E402

# Both fakes serve files from $FAKE_STORE: curl by the URL's last path segment,
# oras by the digest. Every call is logged to $FAKE_STORE/calls.log.
FAKE_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    store = os.environ["FAKE_STORE"]
    args = sys.argv[1:]
    with open(os.path.join(store, "calls.log"), "a") as log:
        log.write(json.dumps(["curl", *args]) + "\\n")
    source = os.path.join(store, "release", args[-1].rsplit("/", 1)[1])
    if not os.path.exists(source):
        sys.exit("curl: (22) The requested URL returned error: 404")
    shutil.copy(source, args[args.index("-o") + 1])
    """
)

FAKE_ORAS = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json, os, shutil, sys
    store = os.environ["FAKE_STORE"]
    args = sys.argv[1:]
    with open(os.path.join(store, "calls.log"), "a") as log:
        log.write(json.dumps(["oras", *args]) + "\\n")
    if os.environ.get("FAKE_ORAS_UNAUTH"):
        sys.exit("Error response from registry: unauthorized: authentication required")
    source = os.path.join(store, "blobs", args[-1].split("@sha256:")[1])
    if not os.path.exists(source):
        sys.exit("Error response from registry: not found")
    shutil.copy(source, args[args.index("--output") + 1])
    """
)

FILE_A = b'{"RECORD_ID": "1"}\n'
FILE_B = b'{"RECORD_ID": "2"}\n{"RECORD_ID": "3"}\n'
EDITED = b'{"RECORD_ID": "edited"}\n'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "repo")
        self.store = os.path.join(self.tmp, "store")
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.root, os.path.join(self.store, "release"),
                          os.path.join(self.store, "blobs"), bin_dir):
            os.makedirs(directory)
        for name, body in (("curl", FAKE_CURL), ("oras", FAKE_ORAS)):
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

        # a.jsonl is released; b.jsonl is uploaded to ghcr.io but not released.
        self.publish("a.jsonl", FILE_A)
        self.blob(FILE_A)
        self.blob(FILE_B)
        self.write_manifest([
            self.entry("a.jsonl", FILE_A, "https://example.com/releases/download/0.1.1/a.jsonl"),
            self.entry("b.jsonl", FILE_B, None),
        ])

    # --- helpers ---

    def entry(self, name, data, url):
        return {"name": name, "size": len(data), "sha256": sha(data),
                "tag": url and "0.1.1", "url": url, "oci": f"ghcr.io/owner/repo/{name}"}

    def write_manifest(self, entries):
        with open(os.path.join(self.root, "manifest.json"), "w") as file:
            json.dump({"files": entries}, file)

    def publish(self, name, data):
        with open(os.path.join(self.store, "release", name), "wb") as file:
            file.write(data)

    def blob(self, data):
        with open(os.path.join(self.store, "blobs", sha(data)), "wb") as file:
            file.write(data)

    def local(self, name):
        path = os.path.join(self.root, "jsonl", name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as file:
            return file.read()

    def write_local(self, name, data):
        os.makedirs(os.path.join(self.root, "jsonl"), exist_ok=True)
        with open(os.path.join(self.root, "jsonl", name), "wb") as file:
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
            code = download_jsonl.main(["--root", self.root, *args])
        return code, out.getvalue()

    # --- tests ---

    def test_downloads_every_entry_into_new_jsonl_dir(self):
        code, output = self.download()
        self.assertEqual(code, 0, output)
        self.assertEqual((self.local("a.jsonl"), self.local("b.jsonl")), (FILE_A, FILE_B))
        self.assertIn("downloaded: a.jsonl", output)
        self.assertIn("downloaded: b.jsonl", output)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "jsonl"))),
                         ["a.jsonl", "b.jsonl"])

    def test_released_entry_uses_url_and_unreleased_uses_oras(self):
        self.download()
        tools = {call[-1].rsplit("/", 1)[-1].split("@")[0]: call[0] for call in self.calls()}
        self.assertEqual(tools["a.jsonl"], "curl")
        self.assertEqual(tools["b.jsonl"], "oras")
        oras = [call for call in self.calls() if call[0] == "oras"][0]
        self.assertEqual(oras[1:4], ["blob", "fetch", "--output"])
        self.assertEqual(oras[-1], f"ghcr.io/owner/repo/b.jsonl@sha256:{sha(FILE_B)}")
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
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "jsonl"))), ["b.jsonl"])

    def test_failed_download_leaves_no_partial_file(self):
        os.remove(os.path.join(self.store, "release", "a.jsonl"))
        code, output = self.download()
        self.assertEqual(code, 1)
        self.assertIn("failed: a.jsonl: curl: (22)", output)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "jsonl"))), ["b.jsonl"])

    def test_oras_login_failure_fails_only_ghcr_entries(self):
        with mock.patch.dict(os.environ, {"FAKE_ORAS_UNAUTH": "1"}):
            code, output = self.download()
        self.assertEqual(code, 1)
        self.assertEqual(self.local("a.jsonl"), FILE_A)
        self.assertIsNone(self.local("b.jsonl"))
        self.assertIn("failed: b.jsonl: Error response from registry: unauthorized", output)
        self.assertIn("oras login ghcr.io", output)

    def test_missing_oras_fails_only_ghcr_entries(self):
        os.remove(os.path.join(self.tmp, "bin", "oras"))
        bin_dir = os.path.join(self.tmp, "bin")
        # Keep python3 for the fake curl, but not the real oras in ~/.local/bin.
        path = bin_dir + os.pathsep + os.path.dirname(sys.executable)
        with mock.patch.dict(os.environ, {"PATH": path}):
            code, output = self.download()
        self.assertEqual(code, 1)
        self.assertEqual(self.local("a.jsonl"), FILE_A)
        self.assertIn("failed: b.jsonl: oras is not on PATH", output)

    def test_files_not_in_manifest_are_left_alone(self):
        self.write_local("notes.jsonl", b'{"x": 1}\n')
        self.download()
        self.assertEqual(self.local("notes.jsonl"), b'{"x": 1}\n')


if __name__ == "__main__":
    unittest.main()
