import os
import unittest

import appalias_testutil  # noqa: F401

from lib import v2fly

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "v2fly")


def load(name):
    return v2fly.Catalog.load(os.path.join(FIXTURES, name))


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.cat = load("basic.lst")

    def test_sections_normalized(self):
        self.assertEqual(self.cat.names(), ["filtered", "netflix", "netflix-extra", "onlyads"])

    def test_own_rules(self):
        rules = self.cat.lists["netflix"].rules
        self.assertEqual(rules[("domain", "netflix.com")], frozenset())
        self.assertIn(("domain", "nflxvideo.net"), rules)          # minuscules, point final retiré
        self.assertEqual(rules[("full", "api.netflix.com")], frozenset({"ads"}))
        self.assertEqual(rules[("domain", "nflxext.com")], frozenset({"ads"}))  # commentaire retiré
        self.assertIn(("keyword", "netflix"), rules)
        self.assertIn(("regexp", "^nflx[0-9]+\\.example$"), rules)
        self.assertEqual(len(rules), 6)                             # doublon netflix.com retiré

    def test_unknown_lines_counted_not_fatal(self):
        unknown = [line for _n, line in self.cat.lists["netflix"].unknown]
        self.assertEqual(len(unknown), 4)
        self.assertIn("garbage:foo", unknown)
        self.assertIn("domain:bad..name", unknown)
        self.assertTrue(any(line.startswith("'domain:ex") for line in unknown))  # non ASCII : refusé, échappé
        self.assertEqual(len(self.cat.unknown_lines()), 4)

    def test_problems_outside_sections(self):
        reasons = [reason for _n, _l, reason in self.cat.problems]
        self.assertEqual(len(reasons), 3)
        self.assertIn("ligne hors section", reasons)
        self.assertIn("nom de liste invalide", reasons)

    def test_parse_rule_line(self):
        self.assertEqual(v2fly.parse_rule_line("Example.COM. @Ads"),
                         ("rule", "domain", "example.com", frozenset({"ads"})))
        self.assertEqual(v2fly.parse_rule_line("include:x @cn @-ads"),
                         ("include", "x", ((True, "cn"), (False, "ads"))))
        self.assertEqual(v2fly.parse_rule_line("domain:x.example @!cn"),
                         ("rule", "domain", "x.example", frozenset({"!cn"})))
        for bad in ("geosite:x", "domain:", "full:a b", "domain:x.example ads", "keyword:a/b", "domain:é.fr"):
            self.assertIsNone(v2fly.parse_rule_line(bad), bad)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.cat = load("basic.lst")

    def test_include_and_missing(self):
        res = self.cat.resolve("netflix")
        self.assertTrue(res.importable)
        self.assertEqual(res.missing, {"absent-list"})
        self.assertEqual(res.unknown, 4)
        self.assertEqual(res.texts(), [
            "domain:extra.example",
            "domain:extra2.example",
            "domain:netflix.com",
            "domain:nflxext.com",
            "domain:nflxvideo.net",
            "full:api.netflix.com",
            "full:extra.example.net",
            "keyword:netflix",
            "regexp:^nflx[0-9]+\\.example$",
        ])
        self.assertEqual(res.attrs(), {"ads", "cn"})

    def test_include_filters(self):
        res = self.cat.resolve("filtered")
        self.assertEqual(res.texts(), ["domain:extra.example", "domain:extra2.example", "full:extra.example.net"])
        self.assertEqual(res.rules[("domain", "extra2.example")], frozenset({"ads", "cn"}))
        res = self.cat.resolve("onlyads")
        self.assertEqual(res.texts(), ["domain:extra2.example", "domain:nflxext.com", "full:api.netflix.com"])
        self.assertEqual(res.missing, {"absent-list"})

    def test_ref_filters(self):
        res = self.cat.resolve_ref("netflix@-ads")
        self.assertNotIn("domain:nflxext.com", res.texts())
        self.assertNotIn("full:api.netflix.com", res.texts())
        self.assertNotIn("domain:extra2.example", res.texts())
        self.assertIn("domain:netflix.com", res.texts())
        res = self.cat.resolve_ref("netflix@cn@-ads")
        self.assertEqual(res.texts(), ["domain:extra.example"])

    def test_refs_union(self):
        res = self.cat.resolve_refs(["netflix-extra@cn", "onlyads"])
        self.assertEqual(res.texts(), ["domain:extra.example", "domain:extra2.example",
                                       "domain:nflxext.com", "full:api.netflix.com"])

    def test_invalid_refs(self):
        for bad in ("../netflix", "netflix ads", "Netflix", "netflix@", "netflix@!cn", "a,b", "", "x" * 129):
            with self.assertRaises(v2fly.RefError, msg=bad):
                v2fly.parse_ref(bad)
        with self.assertRaises(KeyError):
            self.cat.resolve_ref("absent-list")


class CycleTests(unittest.TestCase):
    def test_cycle_broken_with_path(self):
        cat = load("cycle.lst")
        res = cat.resolve("a")
        self.assertFalse(res.importable)
        self.assertEqual(res.broken, "cycle: a > b > c > a")
        self.assertEqual(res.rules, {})
        self.assertFalse(cat.resolve("b").importable)
        self.assertFalse(cat.resolve("top").importable)
        self.assertIn("a > b > c > a", cat.resolve("top").broken)
        self.assertEqual(cat.resolve("self").broken, "cycle: self > self")
        ok = cat.resolve("ok")
        self.assertTrue(ok.importable)
        self.assertEqual(ok.texts(), ["domain:leaf.example", "domain:ok.example"])

    def test_cycle_from_other_entry(self):
        cat = load("cycle.lst")
        self.assertEqual(cat.resolve("top").broken, "cycle: a > b > c > a")
        self.assertEqual(cat.resolve("c").broken, "cycle: a > b > c > a")

    def test_depth_limit_not_memoized(self):
        n = 40
        text = "".join("[l%d]\nh%d.example\ninclude:l%d\n" % (i, i, i + 1) for i in range(n - 1))
        text += "[l%d]\nh%d.example\n" % (n - 1, n - 1)
        cat = v2fly.Catalog.parse(text)
        first = cat.resolve("l0")
        self.assertFalse(first.importable)
        self.assertIn("depth > 32", first.broken)
        # l8 -> l39 : 32 listes, dans la limite ; l7 -> l39 : 33 listes, au-delà.
        self.assertTrue(cat.resolve("l8").importable)
        self.assertEqual(len(cat.resolve("l8").rules), 32)
        self.assertFalse(cat.resolve("l7").importable)

    def test_memoization(self):
        cat = load("basic.lst")
        self.assertIs(cat.resolve("netflix-extra"), cat.resolve("netflix-extra"))


class RealCatalogTests(unittest.TestCase):
    """Le catalogue embarqué s'analyse sans problème ni liste cassée."""

    def test_embedded_catalog(self):
        path = os.path.join(appalias_testutil.SCRIPTS, "catalog", "v2fly.lst")
        cat = v2fly.Catalog.load(path)
        self.assertEqual(cat.problems, [])
        self.assertGreater(len(cat.names()), 100)
        broken = [n for n in cat.names() if not cat.resolve(n).importable]
        self.assertEqual(broken, [])
        self.assertIn("domain:netflix.com", cat.resolve("netflix").texts())


if __name__ == "__main__":
    unittest.main()
