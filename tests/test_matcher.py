import logging
import unittest

import appalias_testutil  # noqa: F401

from lib.matcher import Matcher, parse_rule


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("appalias.test.matcher")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def make(self, rules):
        return Matcher(rules, logger=self.logger)

    def test_domain_prefix_and_bare(self):
        m = self.make({"APP_A": ["domain:example.com"], "APP_B": ["example.org"]})
        self.assertEqual(m.match_name("example.com"), {"APP_A"})
        self.assertEqual(m.match_name("www.example.com"), {"APP_A"})
        self.assertEqual(m.match_name("a.b.c.example.com"), {"APP_A"})
        self.assertEqual(m.match_name("cdn.example.org"), {"APP_B"})
        self.assertEqual(m.match_name("example.com.evil.test"), set())
        self.assertEqual(m.match_name("notexample.com"), set())

    def test_full(self):
        m = self.make({"APP_A": ["full:api.example.com"]})
        self.assertEqual(m.match_name("api.example.com"), {"APP_A"})
        self.assertEqual(m.match_name("x.api.example.com"), set())
        self.assertEqual(m.match_name("example.com"), set())

    def test_case_and_trailing_dot(self):
        m = self.make({"APP_A": ["Domain:Example.COM."], "APP_B": ["full:API.example.net."]})
        self.assertEqual(m.match_name("WWW.EXAMPLE.com."), {"APP_A"})
        self.assertEqual(m.match_name("api.EXAMPLE.net."), {"APP_B"})

    def test_multiple_tables(self):
        m = self.make({"APP_A": ["example.com"], "APP_B": ["full:www.example.com"]})
        self.assertEqual(m.match_name("www.example.com"), {"APP_A", "APP_B"})
        self.assertEqual(m.match(["nothing.test", "www.example.com"]), {"APP_A", "APP_B"})

    def test_keyword_regexp_ignored(self):
        with self.assertLogs(self.logger, level="WARNING") as cm:
            m = self.make({"APP_A": ["keyword:video", "regexp:^.*\\.example\\.com$"]})
        self.assertEqual(len(cm.records), 2)
        self.assertEqual(m.match_name("video.example.com"), set())
        self.assertEqual(len(m.ignored), 2)
        self.assertFalse(m)

    def test_unknown_prefix_and_empty(self):
        with self.assertLogs(self.logger, level="WARNING"):
            m = self.make({"APP_A": ["geosite:test", "", "  ", "domain:", None]})
        self.assertEqual(m.match_name("test"), set())
        self.assertEqual(m.match_name(""), set())

    def test_parse_rule(self):
        self.assertEqual(parse_rule("example.com"), ("domain", "example.com"))
        self.assertEqual(parse_rule("*.example.com"), ("domain", "example.com"))
        self.assertEqual(parse_rule(" full:A.example.com. "), ("full", "a.example.com"))
        self.assertEqual(parse_rule("keyword:abc"), ("keyword", "abc"))
        self.assertIsNone(parse_rule(""))


if __name__ == "__main__":
    unittest.main()
