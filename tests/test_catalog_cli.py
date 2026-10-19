import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest

import appalias_testutil  # noqa: F401

import catalog

LST = (
    "[cdnapp]\n"
    "domain:cloudfront.net\n"
    "d123.cloudfront.net\n"
    "full:edge.akamaized.net\n"
    "app.example\n"
    "ads.app.example @ads\n"
    "keyword:app\n"
    "regexp:^a{1,2}\\.example$\n"
    "include:shared-part\n"
    "[shared-part]\n"
    "part.example @cn\n"
    "[loop]\n"
    "include:loop\n"
    "[media-hub]\n"
    "video.example\n"
) + "".join("[big%02d]\nbig%02d.example\nshared.example\n" % (i, i) for i in range(30))


class CatalogCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "catalog")
        os.mkdir(self.dir)
        self.cache = os.path.join(self.tmp.name, "catalog.index.json")
        self.write_catalog(LST)

    def tearDown(self):
        self.tmp.cleanup()

    def write_catalog(self, text, manifest_sha=None):
        raw = text.encode("ascii")
        with open(os.path.join(self.dir, "v2fly.lst"), "wb") as fh:
            fh.write(raw)
        manifest = {"commit": "f" * 40, "date": "2026-10-17T00:00:00Z", "license": "MIT",
                    "lst_sha256": manifest_sha or hashlib.sha256(raw).hexdigest(),
                    "counts": {"lists_kept": 34}}
        with open(os.path.join(self.dir, "v2fly.manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        with open(os.path.join(self.dir, "LICENSE.v2fly"), "w", encoding="utf-8") as fh:
            fh.write("MIT License\n")

    def run_cli(self, *argv):
        return catalog.run(list(argv), catalog_dir=self.dir, cache_path=self.cache)

    def test_rejects_bad_arguments(self):
        for q in ("../etc", "a b", "net!flix", "Netflix", "a/b", "x" * 65, "", "$(id)", "a;b"):
            self.assertIn("error", self.run_cli("search", q), q)
        for ref in ("../cdnapp", "cdn app", "cdnapp!", "cdnapp@!cn", "CDNAPP", "cdnapp@", "", "a b,c"):
            self.assertIn("error", self.run_cli("preview", ref), ref)
            self.assertIn("error", self.run_cli("resolve", ref), ref)
        self.assertIn("error", self.run_cli("search", "a", "b"))
        self.assertIn("error", self.run_cli("info", "x"))
        self.assertIn("error", self.run_cli("search"))
        self.assertIn("error", self.run_cli("--cache=/tmp/x", "info"))
        self.assertIn("error", self.run_cli())
        self.assertIn("error", self.run_cli("preview", "cdnapp,loop"))
        self.assertIn("error", self.run_cli("resolve", ",".join("big%02d" % i for i in range(30)) + ",a,b,c"))

    def test_info(self):
        info = self.run_cli("info")
        self.assertTrue(info["integrity"])
        self.assertTrue(info["license_file"])
        self.assertEqual(info["commit"], "f" * 40)
        self.assertEqual(info["max_import"], 1000)

    def test_search_by_name_and_domain(self):
        out = self.run_cli("search", "cdnapp")
        self.assertEqual(out["results"][0]["name"], "cdnapp")
        self.assertEqual(out["results"][0]["match"], "name")
        self.assertEqual(out["results"][0]["includes"], ["shared-part"])
        self.assertEqual(out["results"][0]["attrs"], ["ads", "cn"])
        out = self.run_cli("search", "www.video.example")
        self.assertEqual([(r["name"], r["via"]) for r in out["results"]], [("media-hub", "domain:video.example")])
        out = self.run_cli("search", "edge.akamaized.net")
        self.assertEqual(out["results"][0]["name"], "cdnapp")
        self.assertEqual(out["results"][0]["via"], "full:edge.akamaized.net")
        self.assertEqual(self.run_cli("search", "nothing-here")["results"], [])

    def test_search_max_results(self):
        out = self.run_cli("search", "shared.example")
        self.assertEqual(len(out["results"]), 20)
        out = self.run_cli("search", "big")
        self.assertEqual(len(out["results"]), 20)

    def test_preview_flags(self):
        out = self.run_cli("preview", "cdnapp")
        self.assertTrue(out["importable"])
        self.assertEqual(out["suggested_name"], "CDNAPP")
        rules = {r["rule"]: r for r in out["rules"]}
        self.assertEqual(rules["domain:cloudfront.net"]["refused"], "shared")
        self.assertTrue(rules["domain:cloudfront.net"]["shared"])
        self.assertIsNone(rules["domain:d123.cloudfront.net"]["refused"])
        # sous-domaine dédié : non marqué « partagé »
        self.assertFalse(rules["domain:d123.cloudfront.net"]["shared"])
        self.assertFalse(rules["full:edge.akamaized.net"]["shared"])
        self.assertIsNone(rules["full:edge.akamaized.net"]["refused"])
        self.assertFalse(rules["domain:app.example"]["shared"])
        self.assertEqual(rules["domain:ads.app.example"]["attrs"], ["ads"])
        self.assertEqual(rules["domain:part.example"]["attrs"], ["cn"])
        self.assertTrue(rules["keyword:app"]["unapplied"])
        self.assertEqual(rules["regexp:^a{1,2}\\.example$"]["refused"], "comma")
        self.assertEqual(out["count"], 8)
        out = self.run_cli("preview", "cdnapp@-ads@-cn")
        self.assertNotIn("domain:ads.app.example", [r["rule"] for r in out["rules"]])
        self.assertNotIn("domain:part.example", [r["rule"] for r in out["rules"]])

    def test_broken_and_unknown(self):
        out = self.run_cli("preview", "loop")
        self.assertFalse(out["importable"])
        self.assertEqual(out["broken"], "cycle: loop > loop")
        self.assertEqual(out["rules"], [])
        self.assertIn("error", self.run_cli("preview", "absent"))

    def test_resolve(self):
        out = self.run_cli("resolve", "media-hub,shared-part@cn,media-hub")
        self.assertEqual(out["refs"], ["media-hub", "shared-part@cn"])
        self.assertEqual(out["rules"], ["domain:part.example", "domain:video.example"])
        self.assertEqual(out["revision"], "f" * 40)
        self.assertFalse(self.run_cli("resolve", "media-hub,loop")["importable"])

    def test_cache(self):
        self.run_cli("search", "cdnapp")
        with open(self.cache, "r", encoding="utf-8") as fh:
            first = json.load(fh)
        self.assertEqual(first["lst_sha256"], hashlib.sha256(LST.encode()).hexdigest())
        self.assertIn("domain:video.example", first["domains"])
        # Cache valide : réutilisé tel quel.
        first["lists"]["cdnapp"]["rules"] = 999
        with open(self.cache, "w", encoding="utf-8") as fh:
            json.dump(first, fh)
        self.assertEqual(self.run_cli("search", "cdnapp")["results"][0]["rules"], 999)
        # Nouveau .lst : index reconstruit.
        self.write_catalog(LST + "[newlist]\nnew.example\n")
        self.assertEqual(self.run_cli("search", "newlist")["results"][0]["name"], "newlist")
        self.assertEqual(self.run_cli("search", "cdnapp")["results"][0]["rules"], 8)

    def test_unwritable_cache(self):
        out = catalog.run(["search", "cdnapp"], catalog_dir=self.dir,
                          cache_path=os.path.join(self.tmp.name, "absent", "index.json"))
        self.assertEqual(out["results"][0]["name"], "cdnapp")

    def test_integrity_failure(self):
        self.write_catalog(LST, manifest_sha="0" * 64)
        self.assertFalse(self.run_cli("info")["integrity"])
        self.assertIn("error", self.run_cli("search", "cdnapp"))
        self.assertIn("error", self.run_cli("preview", "cdnapp"))
        self.assertIn("error", self.run_cli("resolve", "cdnapp"))

    def test_popular_filters_missing(self):
        with open(os.path.join(self.dir, "popular.json"), "w", encoding="utf-8") as fh:
            json.dump({"categories": [{"name": "Streaming", "lists": ["media-hub", "absent", "../x"]},
                                      {"name": "Empty", "lists": ["nope"]}]}, fh)
        out = self.run_cli("popular")
        self.assertEqual([c["name"] for c in out["categories"]], ["Streaming"])
        self.assertEqual([i["name"] for i in out["categories"][0]["lists"]], ["media-hub"])
        self.assertEqual(out["missing"], ["absent", "nope"])
        os.unlink(os.path.join(self.dir, "popular.json"))
        self.assertIn("error", self.run_cli("popular"))

    def test_embedded_popular(self):
        # Chaque nom de popular.json existe dans l'instantané embarqué.
        from lib import v2fly
        with open(os.path.join(catalog.CATALOG_DIR, "popular.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cat = v2fly.Catalog.load(os.path.join(catalog.CATALOG_DIR, "v2fly.lst"))
        names = [n for c in data["categories"] for n in c["lists"]]
        self.assertEqual([c["name"] for c in data["categories"]],
                         ["Streaming", "Music", "Gaming", "Messaging", "Social", "Productivity"])
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertIn(name, cat.lists, name)
            self.assertTrue(cat.resolve(name).importable, name)
        out = catalog.run(["popular"], cache_path=self.cache)
        self.assertEqual(out["missing"], [])

    def test_main_prints_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(catalog.main(["search", "a b"]), 0)
        self.assertIn("error", json.loads(buf.getvalue()))

    def test_embedded_catalog(self):
        out = catalog.run(["preview", "netflix"], cache_path=self.cache)
        self.assertTrue(out["importable"])
        self.assertEqual(out["suggested_name"], "NETFLIX")
        self.assertIn("domain:netflix.com", [r["rule"] for r in out["rules"]])
        out = catalog.run(["search", "nflxvideo.net"], cache_path=self.cache)
        self.assertIn("netflix", [r["name"] for r in out["results"]])
        self.assertTrue(catalog.run(["info"], cache_path=self.cache)["integrity"])


if __name__ == "__main__":
    unittest.main()
