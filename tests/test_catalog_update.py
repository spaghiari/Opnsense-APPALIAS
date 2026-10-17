"""Mise à jour en ligne du catalogue v2fly (réseau simulé, client injectable)."""

import base64
import io
import json
import os
import shutil
import tempfile
import unittest

import appalias_testutil  # noqa: F401
from test_catalog_snapshot import COMMIT, build_archive, load_tool

import catalog as catmod
import catalog_update as upd
from lib import v2fly_snapshot as snap


class FakeResponse:
    def __init__(self, url, body, length=None):
        self.url = url
        self.body = io.BytesIO(body)
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def geturl(self):
        return self.url

    def read(self, n=-1):
        return self.body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Réponses par URL ; redirect = URL finale rendue par geturl()."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.requests.append(url)
        if url not in self.routes:
            raise OSError("no route: %s" % url)
        body, final = self.routes[url]
        return FakeResponse(final or url, body)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.archive = os.path.join(self.tmp, "src.tar.gz")
        build_archive(self.archive)
        with open(self.archive, "rb") as fh:
            self.archive_bytes = fh.read()
        self.embedded = os.path.join(self.tmp, "embedded")
        self.online = os.path.join(self.tmp, "online")
        os.makedirs(self.embedded)
        load_tool().snapshot(self.archive, COMMIT, self.embedded)
        # instantané embarqué plus ancien que le téléchargement simulé
        manifest = catmod.read_manifest(self.embedded)
        manifest["commit"] = "f" * 40
        manifest["date"] = "2000-01-01T00:00:00Z"
        with open(os.path.join(self.embedded, catmod.MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        self.popular(["netflix"])
        self.lock = os.path.join(self.tmp, "update.lock")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def popular(self, names):
        with open(os.path.join(self.embedded, catmod.POPULAR_NAME), "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "categories": [{"name": "x", "lists": names}]}, fh)

    def opener(self, archive=None, api_final=None):
        return FakeOpener({
            upd.API_URL: (json.dumps({"sha": COMMIT}).encode(), api_final),
            upd.ARCHIVE_URL % COMMIT: (archive if archive is not None else self.archive_bytes, None),
        })

    def update(self, opener, **kw):
        return upd.update(opener=opener, online_dir=self.online, embedded_dir=self.embedded,
                          lock_path=self.lock, min_lists=1, ids=None, now=1_000_000, **kw)

    def test_update_selects_online_and_reset(self):
        out = self.update(self.opener())
        self.assertEqual(out["result"], "updated", out)
        self.assertEqual(out["origin"], "online")
        manifest = catmod.read_manifest(self.online)
        self.assertEqual((manifest["source"], manifest["commit"]), ("online", COMMIT))
        self.assertEqual(manifest["archive_sha256"], snap.sha256_file(self.archive))
        self.assertEqual(catmod.select_dir(self.embedded, self.online), (self.online, "online"))
        # même génération que l'outil du dépôt
        with open(os.path.join(self.online, "v2fly.lst"), "rb") as a, \
                open(os.path.join(self.embedded, "v2fly.lst"), "rb") as b:
            self.assertEqual(a.read(), b.read())
        info = catmod.run(["info"], cache_path=os.path.join(self.tmp, "c.json"), online_dir=self.online)
        self.assertTrue(info["integrity"])
        # déjà à jour : rien à télécharger
        again = self.update(self.opener())
        self.assertEqual(again["result"], "unchanged")
        self.assertEqual(upd.reset(self.online), {"result": "reset", "removed": 3})
        self.assertEqual(catmod.select_dir(self.embedded, self.online)[1], "embedded")

    def test_online_ignored_when_tampered_or_older(self):
        self.assertEqual(self.update(self.opener())["result"], "updated")
        with open(os.path.join(self.online, "v2fly.lst"), "a", encoding="ascii") as fh:
            fh.write("[evil]\nexample.com\n")
        self.assertEqual(catmod.select_dir(self.embedded, self.online)[1], "embedded")

    def test_sanity_failure_keeps_previous(self):
        self.assertEqual(self.update(self.opener())["result"], "updated")
        before = catmod.read_manifest(self.online)
        self.popular(["netflix", "absent-list"])
        # nouveau commit annoncé, archive identique : le contrôle échoue
        other = "1" * 40
        opener = FakeOpener({upd.API_URL: (json.dumps({"sha": other}).encode(), None),
                             upd.ARCHIVE_URL % other: (self.archive_bytes, None)})
        out = self.update(opener)
        self.assertEqual(out["result"], "failed")
        self.assertTrue(out["error"])
        self.assertEqual(catmod.read_manifest(self.online), before)

    def test_too_large(self):
        with self.assertRaises(upd.UpdateError):
            upd.http_get(upd.ARCHIVE_URL % COMMIT, 100, self.opener())
        out = self.update(self.opener(archive=b"x" * (upd.MAX_ARCHIVE + 1)))
        self.assertEqual(out["result"], "failed")
        self.assertIn("too large", out["error"])
        self.assertFalse(os.path.exists(self.online))

    def test_hosts(self):
        for bad in ("http://api.github.com/x", "https://evil.example/x", "https://api.github.com:8443/x",
                    "https://user@api.github.com/x", "https://github.com.evil.example/x"):
            with self.assertRaises(upd.UpdateError, msg=bad):
                upd.check_url(bad)
        upd.check_url("https://codeload.github.com/v2fly/domain-list-community/tar.gz/" + COMMIT)
        out = self.update(self.opener(api_final="https://evil.example/commits"))
        self.assertEqual(out["result"], "failed")
        self.assertIn("refused", out["error"])
        handler = upd._SameHostsRedirect()
        with self.assertRaises(upd.UpdateError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/a.tar.gz")

    def test_license_sha_in_manifest(self):
        self.assertEqual(self.update(self.opener())["result"], "updated")
        manifest = catmod.read_manifest(self.online)
        with open(os.path.join(self.online, catmod.LICENSE_NAME), "rb") as fh:
            import hashlib
            self.assertEqual(manifest["license_sha256"], hashlib.sha256(fh.read()).hexdigest())

    def test_symlink_planted_in_workdir(self):
        """Un lien symbolique dans le dossier de travail ne fait jamais lire un
        fichier réservé à root : root ne lit que la sortie du sous-processus."""
        secret = os.path.join(self.tmp, "secret")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("ROOT-ONLY-SECRET\n")
        real_generate = upd.generate

        def planting(archive, commit, workdir, popular, min_lists=upd.MIN_LISTS):
            manifest = real_generate(archive, commit, workdir, popular, min_lists)
            target = os.path.join(workdir, "out", catmod.LICENSE_NAME)
            os.unlink(target)
            try:
                os.symlink(secret, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest("symlink impossible ici : %s" % exc)
            return manifest

        upd.generate = planting
        try:
            out = self.update(self.opener())
        finally:
            upd.generate = real_generate
        self.assertEqual(out["result"], "failed")
        self.assertIn("license", out["error"])
        if os.path.isdir(self.online):
            for name in os.listdir(self.online):
                with open(os.path.join(self.online, name), "rb") as fh:
                    self.assertNotIn(b"ROOT-ONLY-SECRET", fh.read())

    def payload(self):
        raw = upd.run_child(self.archive, COMMIT, tempfile.mkdtemp(dir=self.tmp), ["netflix"], 1, None)
        return json.loads(raw.decode("utf-8"))

    def test_parse_child_output_rejects(self):
        digest = snap.sha256_file(self.archive)
        good = self.payload()
        lst, license_text, manifest = upd.parse_child_output(json.dumps(good).encode(), COMMIT, digest)
        self.assertTrue(license_text.startswith(upd.LICENSE_HEAD % COMMIT))
        self.assertIn("license_sha256", manifest)
        bad_cases = {
            "license": dict(good, license="root:*:0:0:secret\n"),
            "manifest": dict(good, manifest=["not", "a", "dict"]),
            "sha": dict(good, lst=base64.b64encode(b"[x]\nexample.com\n").decode()),
            "commit": dict(good, manifest=dict(good["manifest"], commit="0" * 40)),
            "error": {"ok": False, "error": "boom"},
            "type": ["ok"],
        }
        for label, data in bad_cases.items():
            with self.assertRaises(upd.UpdateError, msg=label):
                upd.parse_child_output(json.dumps(data).encode(), COMMIT, digest)
        with self.assertRaises(upd.UpdateError):
            upd.parse_child_output(b"{" + b" " * upd.MAX_CHILD_OUTPUT + b"}", COMMIT, digest)
        with self.assertRaises(upd.UpdateError):
            upd.parse_child_output(b"not json", COMMIT, digest)

    def test_read_capped_too_large(self):
        class Proc:
            killed = False

            def kill(self):
                Proc.killed = True

        with self.assertRaises(upd.UpdateError):
            upd.read_capped(io.BytesIO(b"x" * 1000), 100, 5, Proc())
        self.assertTrue(Proc.killed)
        self.assertEqual(upd.read_capped(io.BytesIO(b"abc"), 100, 5, Proc()), b"abc")

    def test_main_never_traces(self):
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            upd.main(["bogus"])
        self.assertEqual(json.loads(out.getvalue())["result"], "failed")

    def test_lock(self):
        with upd.Lock(self.lock):
            out = self.update(self.opener())
        self.assertEqual(out["result"], "failed")
        self.assertIn("another", out["error"])


if __name__ == "__main__":
    unittest.main()
