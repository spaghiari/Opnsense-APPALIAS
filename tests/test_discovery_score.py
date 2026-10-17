"""Score et explications de la découverte (fixtures JSON -> propositions)."""

import json
import os
import tempfile
import unittest

import appalias_testutil  # noqa: F401

import discovery as disccli
from lib import config as cfgmod
from lib import discovery as discmod
from lib import score

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "discovery", "discovery.json")
STREAM = "11111111-0000-4000-8000-000000000001"
CHAT = "22222222-0000-4000-8000-000000000002"
PLATFORMS = frozenset(["cloudfront.net"])

CONFIG = {
    "general": {"enabled": False, "discovery_enabled": True,
                "discovery_ignored": ["full:ignored.example.io", "bogus"]},
    "apps": [
        {"uuid": STREAM, "name": "STREAM", "domains": ["full:www.stream.example", "full:web.streamcdn.net"]},
        {"uuid": CHAT, "name": "CHAT", "domains": ["domain:chat.example.net", "keyword:chat-media"]},
    ],
}


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    engine = discmod.Engine(lambda n: set())
    engine.load_dict(raw)
    return engine.to_dict()


def build(domains_index=None, catalog_rules=None):
    cfg = cfgmod.from_dict(CONFIG)
    suffixes = score.Suffixes()
    apps = [score.AppRules(a, PLATFORMS, suffixes, (catalog_rules or {}).get(a.name)) for a in cfg.apps]
    return score.build(load_fixture(), apps, PLATFORMS, suffixes, domains_index or {},
                       ignored=discmod.Ignored(cfg.discovery_ignored))


def by(result, name, app=None):
    return [p for p in result["proposals"] if p["name"] == name and (app is None or p["app"] == app)]


class ScoreTests(unittest.TestCase):
    def test_expected_proposals(self):
        out = build({"domain:nflxso.net": ["netflix"]})
        img = by(out, "img.streamcdn.net", "STREAM")[0]
        self.assertEqual(img["score"], 90)
        self.assertTrue(img["strong"])
        self.assertEqual([r["code"] for r in img["reasons"]], ["cname_neighbor", "same_site"])
        self.assertEqual(img["reasons"][0], {"code": "cname_neighbor", "target": "edge.streamcdn-lb.net",
                                             "name": "web.streamcdn.net"})
        # trois noms du même site pour STREAM : domain: proposé, full: en option
        for name in ("img.streamcdn.net", "api.streamcdn.net", "video.streamcdn.net"):
            item = by(out, name, "STREAM")[0]
            self.assertEqual(item["rule"], "domain:streamcdn.net")
            self.assertEqual(item["options"], ["full:" + name, "domain:streamcdn.net"])
            self.assertTrue(item["checked"])
        self.assertEqual(by(out, "api.streamcdn.net")[0]["score"], 80)

    def test_cooccurrence_lift(self):
        out = build()
        beacon = by(out, "beacon.stats.org")
        self.assertEqual([p["app"] for p in beacon], ["STREAM"])     # CHAT : lift 1
        self.assertEqual(beacon[0]["score"], 40)
        self.assertEqual(beacon[0]["reasons"][0]["code"], "cooccurrence")
        self.assertEqual(beacon[0]["rule"], "full:beacon.stats.org")

    def test_shared_platform_full_unchecked(self):
        item = by(build(), "d111.cloudfront.net")[0]
        self.assertTrue(item["shared"])
        self.assertEqual(item["options"], ["full:d111.cloudfront.net"])
        self.assertFalse(item["checked"])

    def test_keyword_and_site(self):
        item = by(build(), "chat-media.example.net", "CHAT")[0]
        self.assertEqual(item["score"], 65)
        self.assertEqual({r["code"] for r in item["reasons"]}, {"same_site", "keyword"})

    def test_unassigned_ignored_learn_v2fly(self):
        out = build({"domain:nflxso.net": ["netflix"]})
        names = {n["name"]: n for g in out["unassigned"] for n in g["names"]}
        self.assertIn("lonely.example.io", names)
        self.assertTrue(names["lonely.example.io"]["new"])
        self.assertEqual(names["nflxso.net"]["v2fly"], ["netflix"])
        self.assertNotIn("ignored.example.io", names)
        self.assertFalse(by(out, "ignored.example.io"))

    def test_v2fly_imported(self):
        cfg = dict(CONFIG)
        out = build(catalog_rules={"STREAM": {"stream": ["domain:streamcdn.net"]}})
        reasons = [r["code"] for r in by(out, "api.streamcdn.net", "STREAM")[0]["reasons"]]
        self.assertIn("v2fly_imported", reasons)
        self.assertEqual(by(out, "api.streamcdn.net", "STREAM")[0]["score"], 100)   # plafond
        self.assertTrue(cfg)


class BudgetTests(unittest.TestCase):
    def test_timeout_during_regex_search(self):
        """Échéance pendant une recherche (faux _Budget) : regexps abandonnées,
        notation poursuivie, regex_timeout vrai, aucune exception."""
        calls = []

        class Exploding:
            def search(self, text):
                calls.append(text)
                raise score._Budget()

        cfg = cfgmod.from_dict(CONFIG)
        suffixes = score.Suffixes()
        apps = [score.AppRules(a, PLATFORMS, suffixes) for a in cfg.apps]
        apps[1].regexps = [("x+", Exploding()), ("y+", Exploding())]
        out = score.build(load_fixture(), apps, PLATFORMS, suffixes, {})
        self.assertTrue(out["regex_timeout"])
        self.assertEqual(len(calls), 1)                         # plus aucune regexp ensuite
        self.assertEqual(by(out, "img.streamcdn.net", "STREAM")[0]["score"], 90)   # notation continue

    def test_alarm_outside_search_only_marks(self):
        with score.RegexBudget(seconds=5) as budget:
            budget._alarm(None, None)                           # hors recherche : pas d'exception
            self.assertTrue(budget.exhausted)
            self.assertFalse(budget.search(score.safe_regex("a"), "a"))

    def test_deadline_without_alarm(self):
        budget = score.RegexBudget(seconds=0)
        budget.deadline = 0.0
        self.assertFalse(budget.search(score.safe_regex("a"), "a"))
        self.assertTrue(budget.exhausted)


class HelperTests(unittest.TestCase):
    def test_etld_heuristic_and_psl(self):
        h = score.Suffixes()
        self.assertEqual(h.site("a.b.example.co.uk"), "example.co.uk")
        self.assertEqual(h.site("img.example.com"), "example.com")
        psl = score.Suffixes("// x\ncom\n*.ck\n!www.ck\nblogspot.com\n")
        self.assertEqual(psl.site("a.foo.blogspot.com"), "foo.blogspot.com")
        self.assertEqual(psl.site("a.b.x.ck"), "b.x.ck")
        self.assertEqual(psl.site("www.ck"), "www.ck")

    def test_safe_regex(self):
        self.assertIsNotNone(score.safe_regex(r"^cdn[0-9]+\.example\.com$"))
        for bad in (r"(a+)+$", r"(x*)*", r"(ab+){2,}", "a" * 300, "(", ""):
            self.assertIsNone(score.safe_regex(bad), bad)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.data = os.path.join(self.tmp.name, "discovery.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(CONFIG, fh)
        with open(FIXTURE, "r", encoding="utf-8") as src, open(self.data, "w", encoding="utf-8") as dst:
            dst.write(src.read())

    def tearDown(self):
        self.tmp.cleanup()

    def test_list(self):
        out = disccli.cmd_list(self.config, self.data, now=1_000_000)
        self.assertTrue(out["enabled"] and out["data"])
        self.assertEqual(out["uncovered"], 9)
        self.assertEqual(out["observed"], 11)
        self.assertEqual(out["dropped"], 3)
        self.assertGreater(out["counts"][STREAM], 3)
        self.assertEqual(out["learn"]["new"], ["lonely.example.io"])

    def test_list_disabled_or_corrupt(self):
        cfg = dict(CONFIG, general={"discovery_enabled": False})
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        self.assertEqual(disccli.cmd_list(self.config, self.data)["proposals"], [])
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(CONFIG, fh)
        with open(self.data, "w", encoding="utf-8") as fh:
            fh.write("garbage")
        self.assertFalse(disccli.cmd_list(self.config, self.data)["data"])

    def test_ctl(self):
        calls = []

        class Res:
            returncode = 0

        def run(argv, **kw):
            calls.append(argv)
            return Res()

        self.assertTrue(disccli.cmd_ctl("learn:120:" + STREAM, self.data, run=run, now=1000.0))
        with open(discmod.ctl_path_for(self.data), "r", encoding="utf-8") as fh:
            cmd = json.load(fh)
        self.assertEqual(cmd, {"cmd": "learn", "until": 1120.0, "app_uuid": STREAM})
        self.assertEqual(calls[0][1], "onectl")
        Res.returncode = 1
        self.assertTrue(disccli.cmd_ctl("purge", self.data, run=run))
        self.assertFalse(os.path.exists(self.data))           # purge même relais arrêté
        self.assertFalse(disccli.cmd_ctl("dump", self.data, run=run))
        for bad in ("learn:900", "learn:0", "rm -rf", "learn:10:x", "dump;id"):
            with self.assertRaises(ValueError, msg=bad):
                disccli.cmd_ctl(bad, self.data, run=run)


if __name__ == "__main__":
    unittest.main()
