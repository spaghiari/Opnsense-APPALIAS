import contextlib
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest

import appalias_testutil  # noqa: F401
from appalias_testutil import ROOT, SCRIPTS

from lib import v2fly

CATALOG = os.path.join(SCRIPTS, "catalog")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "v2fly-snapshot")
COMMIT = "0123456789abcdef0123456789abcdef01234567"
MTIME = 1760659200  # 2025-10-17T00:00:00Z


def load_tool():
    path = os.path.join(ROOT, "tools", "v2fly-snapshot.py")
    spec = importlib.util.spec_from_file_location("v2fly_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_archive(path, extra=None):
    """Archive tar au format GitHub, déterministe (USTAR, dates fixes)."""
    top = "domain-list-community-%s" % COMMIT
    with open(os.path.join(FIXTURE, "LICENSE"), "rb") as fh:
        files = [("LICENSE", fh.read())]
    for name in sorted(os.listdir(os.path.join(FIXTURE, "data"))):
        with open(os.path.join(FIXTURE, "data", name), "rb") as fh:
            files.append(("data/" + name, fh.read()))
    files.append(("README.md", b"not extracted\n"))
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for dirname in (top, top + "/data"):
            info = tarfile.TarInfo(dirname)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = MTIME
            tar.addfile(info)
        for name, data in files:
            info = tarfile.TarInfo("%s/%s" % (top, name))
            info.size = len(data)
            info.mode = 0o644
            info.mtime = MTIME
            tar.addfile(info, io.BytesIO(data))
        for info, data in (extra or ()):
            info.name = info.name.replace("@TOP@", top)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class EmbeddedCatalogTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(CATALOG, "v2fly.manifest.json"), "r", encoding="utf-8") as fh:
            self.manifest = json.load(fh)

    def test_lst_matches_manifest(self):
        path = os.path.join(CATALOG, "v2fly.lst")
        self.assertEqual(sha256(path), self.manifest["lst_sha256"])
        self.assertEqual(os.path.getsize(path), self.manifest["lst_bytes"])
        self.assertLessEqual(self.manifest["lst_bytes"], 1572864)

    def test_license_present(self):
        with open(os.path.join(CATALOG, "LICENSE.v2fly"), "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c)", text)
        self.assertIn("Permission is hereby granted, free of charge", text)
        self.assertIn(self.manifest["commit"], text)
        self.assertEqual(self.manifest["license"], "MIT")

    def test_no_excluded_list(self):
        tool = load_tool()
        cat = v2fly.Catalog.load(os.path.join(CATALOG, "v2fly.lst"))
        excluded = [n for n in cat.names() if tool.excluded_reason(n) is not None]
        self.assertEqual(excluded, [])
        for name in cat.names():
            self.assertFalse(name.startswith(("category-", "geolocation-", "tld-")), name)
            self.assertNotIn(name, ("cn", "private"))
            self.assertNotIn("!", name)

    def test_manifest_pinned_commit(self):
        self.assertRegex(self.manifest["commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(self.manifest["archive_sha256"], r"^[0-9a-f]{64}$")
        with open(os.path.join(ROOT, "docs", "maintenance.md"), "r", encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn(self.manifest["commit"], doc)
        self.assertIn(self.manifest["archive_sha256"], doc)


class SnapshotToolTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self.tmp.name, "archive.tar")
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts):
        with open(os.path.join(*parts), "rb") as fh:
            return fh.read()

    def test_expected_output_byte_for_byte(self):
        build_archive(self.archive)
        digest = sha256(self.archive)
        manifest = self.tool.snapshot(self.archive, COMMIT, self.out, expected_sha256=digest)
        self.assertEqual(manifest["archive_sha256"], digest)
        expected = os.path.join(FIXTURE, "expected")
        self.assertEqual(self.read(self.out, "v2fly.lst"), self.read(expected, "v2fly.lst"))
        self.assertEqual(self.read(self.out, "LICENSE.v2fly"), self.read(expected, "LICENSE.v2fly"))
        template = self.read(expected, "v2fly.manifest.json.in").replace(b"@ARCHIVE_SHA256@", digest.encode())
        self.assertEqual(self.read(self.out, "v2fly.manifest.json"), template)

    def test_sha256_mismatch_refused(self):
        build_archive(self.archive)
        with self.assertRaises(self.tool.SnapshotError):
            self.tool.snapshot(self.archive, COMMIT, self.out, expected_sha256="0" * 64)
        self.assertFalse(os.path.exists(self.out))

    def test_traversal_refused(self):
        info = tarfile.TarInfo("@TOP@/../evil")
        info.size = 4
        build_archive(self.archive, extra=[(info, b"evil")])
        with self.assertRaises(self.tool.SnapshotError):
            self.tool.snapshot(self.archive, COMMIT, self.out)

    def test_symlink_refused(self):
        info = tarfile.TarInfo("@TOP@/data/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        build_archive(self.archive, extra=[(info, None)])
        with self.assertRaises(self.tool.SnapshotError):
            self.tool.snapshot(self.archive, COMMIT, self.out)

    def test_cli_requires_sha256(self):
        build_archive(self.archive)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.tool.main(["--commit", COMMIT, "--archive", self.archive, "--out", self.out])
            with self.assertRaises(SystemExit):
                self.tool.main(["--commit", "abc", "--archive", self.archive, "--out", self.out, "--tofu"])
        self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
