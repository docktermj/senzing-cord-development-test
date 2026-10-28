"""Tests for upload_jsonl.py, run against fake oras and gh executables.

Run with:
  python3 -m unittest discover -s .claude/skills/upload-jsonl -p 'test_*.py'
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
import upload_jsonl  # noqa: E402

REGISTRY = "ghcr.io/owner/repo"

# A fake oras that keeps a registry in $FAKE_ORAS_STATE and logs every call.
FAKE_ORAS = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import hashlib, json, os, sys
    state = os.environ["FAKE_ORAS_STATE"]
    args = sys.argv[1:]
    with open(os.path.join(state, "calls.log"), "a") as log:
        log.write(json.dumps(args) + "\\n")
    def path(ref):
        return os.path.join(state, ref.replace("/", "_").replace(":", "@"))
    if os.environ.get("FAKE_ORAS_UNAUTH"):
        sys.exit("Error response from registry: unauthorized: authentication required")
    if args[:2] == ["manifest", "fetch"]:
        ref = args[-1]
        if not os.path.exists(path(ref)):
            sys.exit(f"Error response from registry: failed to find {ref}: not found")
        manifest = open(path(ref)).read()
        if "--descriptor" in args:
            print(json.dumps({"digest": "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()}))
        else:
            print(manifest)
        sys.exit(0)
    if args[0] == "push":
        ref, spec = args[1], args[-1]
        name = spec.rsplit(":", 1)[0]
        if name in os.environ.get("FAKE_ORAS_FAIL", "").split(","):
            sys.exit("Error: upload timed out")
        data = open(name, "rb").read()
        layer = {"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}
        with open(path(ref), "w") as f:
            json.dump({"layers": [layer]}, f)
        sys.exit(0)
    sys.exit("fake oras: unsupported " + " ".join(args))
    """
)

FAKE_GH = textwrap.dedent(
    """\
    #!/bin/sh
    printf 'HTTP/2.0 200 OK\\nX-Oauth-Scopes: %s\\n\\n{}\\n' "$FAKE_GH_SCOPES"
    """
)

LINES_A = b'{"RECORD_ID": "1"}\n{"RECORD_ID": "2"}\n'
LINES_B = b'{"RECORD_ID": "1"}\n{"RECORD_ID": "3"}\n'


def read(path):
    with open(path) as file:
        return file.read()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "repo")
        self.state = os.path.join(self.tmp, "registry")
        bin_dir = os.path.join(self.tmp, "bin")
        for directory in (self.root, self.state, bin_dir, os.path.join(self.root, "jsonl")):
            os.makedirs(directory, exist_ok=True)
        for name, body in (("oras", FAKE_ORAS), ("gh", FAKE_GH)):
            exe = os.path.join(bin_dir, name)
            with open(exe, "w") as file:
                file.write(body)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(
            ["git", "-C", self.root, "remote", "add", "origin", "git@github.com:Owner/Repo.git"],
            check=True,
        )
        env = {
            "PATH": bin_dir + os.pathsep + os.environ["PATH"],
            "FAKE_ORAS_STATE": self.state,
            "FAKE_GH_SCOPES": "repo, write:packages",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers ---

    def write_jsonl(self, name, data):
        with open(os.path.join(self.root, "jsonl", name), "wb") as file:
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

    def pushes(self):
        log = os.path.join(self.state, "calls.log")
        if not os.path.exists(log):
            return []
        calls = [json.loads(line) for line in read(log).splitlines()]
        return [call for call in calls if call[0] == "push"]

    def seed_registry(self, name, data):
        """Put a file in the fake registry as if it had been pushed earlier."""
        ref = f"{REGISTRY}/{name}:sha256-{sha(data)}"
        path = os.path.join(self.state, ref.replace("/", "_").replace(":", "@"))
        with open(path, "w") as file:
            json.dump({"layers": [{"digest": f"sha256:{sha(data)}", "size": len(data)}]}, file)

    def entry(self, name, data, **extra):
        return {
            "name": name, "size": len(data), "sha256": sha(data), "tag": "0.1.0",
            "url": f"https://example.com/0.1.0/{name}", "oci": f"{REGISTRY}/{name}", **extra,
        }

    def run_upload(self, *args):
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            code = upload_jsonl.main(["--root", self.root, *args])
        return code, out.getvalue()

    # --- tests ---

    def test_new_file_is_pushed_and_added(self):
        self.write_jsonl("a.jsonl", LINES_A)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(
            self.manifest()["a.jsonl"],
            {
                "name": "a.jsonl", "size": len(LINES_A), "sha256": sha(LINES_A),
                "tag": None, "url": None, "oci": f"{REGISTRY}/a.jsonl",
            },
        )
        self.assertIn("## [Unreleased]\n\n### Added\n\n- `a.jsonl`", self.changelog())
        self.assertTrue(self.changelog().startswith("# Changelog\n"))

    def test_push_uses_tag_and_metadata(self):
        self.write_jsonl("a.jsonl", LINES_A)
        self.run_upload()
        self.assertEqual(
            self.pushes(),
            [[
                "push", f"{REGISTRY}/a.jsonl:sha256-{sha(LINES_A)}",
                "--artifact-type", "application/vnd.senzing.cord.v1",
                "--annotation", "org.opencontainers.image.source=https://github.com/Owner/Repo",
                "a.jsonl:application/x-ndjson",
            ]],
        )

    def test_changed_file_is_pushed_and_unreleased(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A, source="https://example.com/a"))
        self.write_jsonl("a.jsonl", LINES_B)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        entry = self.manifest()["a.jsonl"]
        self.assertEqual((entry["sha256"], entry["size"]), (sha(LINES_B), len(LINES_B)))
        self.assertEqual((entry["tag"], entry["url"]), (None, None))
        self.assertEqual(entry["source"], "https://example.com/a")
        self.assertIn("### Changed\n\n- `a.jsonl`", self.changelog())

    def test_current_file_is_not_pushed(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A))
        self.seed_registry("a.jsonl", LINES_A)
        self.write_jsonl("a.jsonl", LINES_A)
        before = read(os.path.join(self.root, "manifest.json"))
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(read(os.path.join(self.root, "manifest.json")), before)
        self.assertIn("current: a.jsonl", output)

    def test_missing_tag_is_repaired_without_changelog(self):
        old = self.entry("a.jsonl", LINES_A, oci="ghcr.io/owner/repo:v0.1.0")
        self.write_manifest(old)
        self.write_jsonl("a.jsonl", LINES_A)
        code, _ = self.run_upload()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.pushes()), 1)
        self.assertEqual(self.manifest()["a.jsonl"], {**old, "oci": f"{REGISTRY}/a.jsonl"})
        self.assertIsNone(self.changelog())

    def test_entry_without_local_file_is_untouched(self):
        other = self.entry("b.jsonl", LINES_B)
        self.write_manifest(other)
        self.write_jsonl("a.jsonl", LINES_A)
        self.run_upload()
        self.assertEqual(self.manifest()["b.jsonl"], other)

    def test_remove_touches_manifest_only(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A), self.entry("b.jsonl", LINES_B))
        code, _ = self.run_upload("--remove", "b.jsonl")
        self.assertEqual(code, 0)
        self.assertEqual(list(self.manifest()), ["a.jsonl"])
        self.assertIn("### Removed\n\n- `b.jsonl`\n", self.changelog())
        self.assertFalse(os.path.exists(os.path.join(self.state, "calls.log")))

    def test_remove_unknown_name_stops(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A))
        self.write_jsonl("c.jsonl", LINES_A)
        code, output = self.run_upload("--remove", "nope.jsonl")
        self.assertEqual(code, 2)
        self.assertIn("nope.jsonl", output)
        self.assertEqual(self.pushes(), [])

    def test_second_run_pushes_nothing(self):
        self.write_jsonl("a.jsonl", LINES_A)
        self.run_upload()
        self.run_upload()
        self.assertEqual(len(self.pushes()), 1)

    def test_changelog_keeps_one_line_per_file(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A))
        self.write_jsonl("a.jsonl", LINES_B)
        self.run_upload()
        self.write_jsonl("a.jsonl", LINES_B + b'{"RECORD_ID": "4"}\n')
        self.run_upload()
        self.assertEqual(self.changelog().count("- `a.jsonl`"), 1)

    def test_added_stays_added_when_changed_again(self):
        self.write_jsonl("a.jsonl", LINES_A)
        self.run_upload()
        self.write_jsonl("a.jsonl", LINES_B)
        self.run_upload()
        changelog = self.changelog()
        self.assertEqual(changelog.count("- `a.jsonl`"), 1)
        self.assertIn(f"### Added\n\n- `a.jsonl` ({len(LINES_B):,} bytes, SHA-256 `{sha(LINES_B)}`)", changelog)
        self.assertNotIn("### Changed", changelog)

    def test_unreleased_goes_above_existing_release(self):
        with open(os.path.join(self.root, "CHANGELOG.md"), "w") as file:
            file.write(
                upload_jsonl.CHANGELOG_HEADER
                + "\n## [0.1.0] - 2026-10-27\n\n### Added\n\n- `old.jsonl`\n\n"
                "[0.1.0]: https://example.com/0.1.0\n"
            )
        self.write_jsonl("a.jsonl", LINES_A)
        self.run_upload()
        changelog = self.changelog()
        self.assertLess(changelog.index("## [Unreleased]"), changelog.index("## [0.1.0]"))
        self.assertTrue(changelog.endswith("[0.1.0]: https://example.com/0.1.0\n"))

    def test_invalid_files_are_refused(self):
        self.write_jsonl("Upper.jsonl", LINES_A)
        self.write_jsonl("bad.jsonl", b'{"RECORD_ID": "1"}\nnot json\n')
        self.write_jsonl("blank.jsonl", b'{"RECORD_ID": "1"}\n\n{"RECORD_ID": "2"}\n')
        self.write_jsonl("good.jsonl", LINES_A)
        code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertEqual(list(self.manifest()), ["good.jsonl"])
        self.assertIn("refused: Upper.jsonl: name must match", output)
        self.assertIn("refused: bad.jsonl: line 2 is not valid JSON", output)
        self.assertIn("refused: blank.jsonl: line 2 is not valid JSON", output)

    def test_oversized_file_is_refused(self):
        self.write_jsonl("a.jsonl", LINES_A)
        with mock.patch.object(upload_jsonl, "MAX_SIZE", 10):
            code, output = self.run_upload()
        self.assertEqual(code, 1)
        self.assertIn("over the 2 GiB limit", output)
        self.assertEqual(self.pushes(), [])

    def test_non_jsonl_files_are_ignored(self):
        self.write_jsonl("notes.txt", b"hello")
        code, output = self.run_upload()
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))
        self.assertIn("no .jsonl files", output)

    def test_unauthenticated_stops_before_changes(self):
        self.write_jsonl("a.jsonl", LINES_A)
        with mock.patch.dict(os.environ, {"FAKE_ORAS_UNAUTH": "1"}):
            code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn("oras login ghcr.io", output)
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))

    def test_missing_write_scope_stops_before_changes(self):
        self.write_jsonl("a.jsonl", LINES_A)
        with mock.patch.dict(os.environ, {"FAKE_GH_SCOPES": "repo, workflow"}):
            code, output = self.run_upload()
        self.assertEqual(code, 2)
        self.assertIn("write:packages", output)
        self.assertEqual(self.pushes(), [])

    def test_failed_push_leaves_entry_unchanged(self):
        self.write_manifest(self.entry("a.jsonl", LINES_A))
        self.write_jsonl("a.jsonl", LINES_B)
        self.write_jsonl("b.jsonl", LINES_A)
        with mock.patch.dict(os.environ, {"FAKE_ORAS_FAIL": "a.jsonl"}):
            code, output = self.run_upload()
        self.assertEqual(code, 1)
        manifest = self.manifest()
        self.assertEqual(manifest["a.jsonl"]["sha256"], sha(LINES_A))
        self.assertIn("b.jsonl", manifest)
        self.assertIn("failed: a.jsonl: Error: upload timed out", output)
        self.assertNotIn("a.jsonl", self.changelog())

    def test_dry_run_changes_nothing(self):
        self.write_jsonl("a.jsonl", LINES_A)
        code, output = self.run_upload("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would push: a.jsonl (added)", output)
        self.assertEqual(self.pushes(), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "manifest.json")))

    def test_manifest_is_sorted_by_name(self):
        self.write_manifest(self.entry("m.jsonl", LINES_B))
        self.write_jsonl("z.jsonl", LINES_A)
        self.write_jsonl("a.jsonl", LINES_B)
        self.run_upload()
        with open(os.path.join(self.root, "manifest.json")) as file:
            names = [entry["name"] for entry in json.load(file)["files"]]
        self.assertEqual(names, ["a.jsonl", "m.jsonl", "z.jsonl"])


if __name__ == "__main__":
    unittest.main()
