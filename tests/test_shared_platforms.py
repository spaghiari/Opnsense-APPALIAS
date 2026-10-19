import os
import re
import tempfile
import unittest

import appalias_testutil  # noqa: F401
from appalias_testutil import ROOT, SCRIPTS

from lib import shared

EXPECTED = {
    "cloudfront.net", "akamaiedge.net", "akamai.net", "akamaized.net", "akamaihd.net",
    "edgekey.net", "edgesuite.net", "fastly.net", "fastlylb.net", "azureedge.net",
    "azurefd.net", "trafficmanager.net", "cloudflare.net", "amazonaws.com",
    "googleusercontent.com", "googleapis.com", "gstatic.com", "1e100.net",
    "edgecastcdn.net", "llnwd.net", "b-cdn.net", "cdn77.org", "incapdns.net",
    "cloudapp.net", "windows.net", "github.io", "herokuapp.com", "workers.dev",
    "pages.dev", "vercel.app", "netlify.app", "appspot.com",
    # espaces intermédiaires multi-locataires
    "s3.amazonaws.com", "s3-accelerate.amazonaws.com", "blob.core.windows.net",
    "storage.googleapis.com",
}

MODEL_PHP = os.path.join(ROOT, "net", "appalias", "src", "opnsense", "mvc", "app", "models",
                         "OPNsense", "AppAlias", "AppAlias.php")


class SharedPlatformsTests(unittest.TestCase):
    def setUp(self):
        self.platforms = shared.load()

    def test_default_file(self):
        self.assertEqual(shared.DEFAULT_PATH, os.path.join(SCRIPTS, "data", "shared_platforms.txt"))
        self.assertEqual(set(self.platforms), EXPECTED)

    def test_php_reads_same_file(self):
        # Source unique : le modèle PHP lit le fichier installé du même chemin relatif.
        with open(MODEL_PHP, "r", encoding="utf-8") as fh:
            php = fh.read()
        match = re.search(r"SHARED_PLATFORMS_FILE = '([^']+)'", php)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1),
                         "/usr/local/opnsense/scripts/OPNsense/AppAlias/data/shared_platforms.txt")

    def test_refused(self):
        p = self.platforms
        self.assertEqual(shared.refused("domain", "cloudfront.net", p), "shared")
        self.assertEqual(shared.refused("domain", "CloudFront.NET.", p), "shared")
        self.assertEqual(shared.refused("domain", "*.cloudfront.net", p), "shared")
        self.assertEqual(shared.refused("domain", "net", p), "tld")
        self.assertEqual(shared.refused("domain", "com", p), "tld")
        self.assertEqual(shared.refused("domain", "akamai.net", p), "shared")
        self.assertEqual(shared.refused("domain", "io", p), "tld")
        self.assertIsNone(shared.refused("domain", "d123.cloudfront.net", p))
        self.assertIsNone(shared.refused("domain", "netflix.com", p))
        self.assertIsNone(shared.refused("domain", "notcloudfront.net", p))
        self.assertIsNone(shared.refused("full", "cloudfront.net", p))
        self.assertIsNone(shared.refused("keyword", "net", p))

    def test_parent_of_extra(self):
        p = shared.load(extra=["cdn.example.org", "  Bad Name  ", "*.edge.example.net."])
        self.assertIn("cdn.example.org", p)
        self.assertIn("edge.example.net", p)
        self.assertNotIn("bad name", p)
        self.assertEqual(shared.refused("domain", "example.org", p), "shared")
        self.assertEqual(shared.refused("domain", "cdn.example.org", p), "shared")
        self.assertIsNone(shared.refused("domain", "x.cdn.example.org", p))

    def test_excluded(self):
        p = shared.load()
        self.assertEqual(shared.excluded("full", "googleapis.com", p), "shared")
        self.assertEqual(shared.excluded("domain", "googleapis.com", p), "shared")
        self.assertEqual(shared.excluded("domain", "com", p), "tld")
        self.assertEqual(shared.excluded("full", "com", p), "tld")
        self.assertIsNone(shared.excluded("domain", "youtubei.googleapis.com", p))
        self.assertIsNone(shared.excluded("full", "yt3.googleusercontent.com", p))
        self.assertIsNone(shared.excluded("domain", "netflix.com", p))
        self.assertIsNone(shared.excluded("keyword", "com", p))
        self.assertIsNone(shared.excluded("regexp", "googleapis.com", p))
        self.assertEqual(shared.excluded("domain", "GoogleAPIs.com.", p), "shared")
        self.assertIsNone(shared.excluded("domain", "YouTubei.GoogleAPIs.com.", p))

    def test_excluded_multitenant(self):
        p = shared.load()
        self.assertEqual(shared.excluded("domain", "s3.amazonaws.com", p), "shared")
        self.assertEqual(shared.excluded("domain", "blob.core.windows.net", p), "shared")
        self.assertEqual(shared.excluded("domain", "core.windows.net", p), "shared")   # parent
        self.assertEqual(shared.excluded("domain", "amazonaws.com", p), "shared")      # parent conservé
        self.assertEqual(shared.excluded("domain", "storage.googleapis.com", p), "shared")
        self.assertEqual(shared.excluded("domain", "github.io", p), "shared")
        self.assertIsNone(shared.excluded("domain", "bucket.s3.amazonaws.com", p))
        self.assertIsNone(shared.excluded("full", "acct.blob.core.windows.net", p))
        self.assertIsNone(shared.excluded("domain", "x.storage.googleapis.com", p))

    def test_normalize_wildcard(self):
        # parité avec AppAlias::normalizeHost / catalogExclusion (retire « *. » en tête)
        p = shared.load()
        self.assertEqual(shared.excluded("domain", "*.googleapis.com", p), "shared")
        self.assertIsNone(shared.excluded("domain", "*.youtubei.googleapis.com", p))
        self.assertEqual(shared.normalize(" *.Example.COM. "), "example.com")
        with open(MODEL_PHP, "r", encoding="utf-8") as fh:
            php = fh.read()
        body = php[php.index("function catalogExclusion"):]
        body = body[:body.index(chr(10) + "    }" + chr(10))]
        self.assertIn("self::normalizeHost($value)", body)

    def test_extra_validation_parity(self):
        # la regex PHP de sharedPlatforms() reproduit à l'identique shared._NAME
        with open(MODEL_PHP, "r", encoding="utf-8") as fh:
            php = fh.read()
        body = php[php.index("function sharedPlatforms()"):]
        body = body[:body.index(chr(10) + "    }" + chr(10))]
        match = re.search(r"preg_match\('/(.+?)/', \$item\)", body)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), shared._NAME.pattern)
        for bad in ["bad name", "-x.org", "a..b", "x" * 64 + ".org"]:
            self.assertIsNone(shared._NAME.match(bad), bad)
            self.assertNotIn(bad, shared.load(extra=[bad]))

    def test_excluded_extra(self):
        p = shared.load(extra=["cdn.example.org"])
        self.assertEqual(shared.excluded("domain", "cdn.example.org", p), "shared")
        self.assertEqual(shared.excluded("full", "example.org", p), "shared")
        self.assertIsNone(shared.excluded("full", "x.cdn.example.org", p))
        # touches (découverte) continue de signaler le sous-domaine
        self.assertTrue(shared.touches("x.cdn.example.org", p))

    def test_touches(self):
        p = self.platforms
        self.assertTrue(shared.touches("cloudfront.net", p))
        self.assertTrue(shared.touches("d123.cloudfront.net", p))
        self.assertTrue(shared.touches("net", p))
        self.assertFalse(shared.touches("netflix.com", p))
        self.assertFalse(shared.touches("notcloudfront.net", p))

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("appalias", level="WARNING"):
                p = shared.load(os.path.join(tmp, "absent.txt"), extra=["cdn.example.org"])
        self.assertEqual(p, frozenset({"cdn.example.org"}))

    def test_parse_comments(self):
        self.assertEqual(shared.parse("# x\nA.example.  # c\n\nbad name\n"), {"a.example"})


if __name__ == "__main__":
    unittest.main()
