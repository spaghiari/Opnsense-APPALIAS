"""Résolution proactive : lecture et bornage des réglages de config.json."""

import unittest

import appalias_testutil  # noqa: F401

from lib import config as cfgmod


def gen(**kw):
    return cfgmod.from_dict({"general": kw})


class ProactiveConfigTests(unittest.TestCase):
    def test_defaults(self):
        for cfg in (gen(), cfgmod.from_dict({})):
            self.assertTrue(cfg.proactive_enabled)
            self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (300, 3600))
            self.assertEqual(cfg.proactive_prefixes, ["www"])

    def test_values_and_bounds(self):
        cfg = gen(proactive_enabled="0", proactive_interval_min=10, proactive_interval_max=100000,
                  ttl_min=86400, ttl_margin=86400)
        self.assertFalse(cfg.proactive_enabled)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (60, 86400))
        cfg = gen(proactive_interval_min=120, proactive_interval_max=600)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (120, 600))
        cfg = gen(proactive_interval_min="abc", proactive_interval_max=None)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (300, 3600))
        cfg = gen(proactive_interval_min=1, proactive_interval_max=1)     # max relevé au minimum de 300
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (60, 300))

    def test_interval_max_below_expiry(self):
        with self.assertLogs("appalias", level="WARNING") as cm:
            cfg = gen(proactive_interval_max=7200, ttl_min=300, ttl_margin=3600)
        self.assertEqual(cfg.proactive_interval_max, 3899)               # strictement sous 3900
        self.assertEqual(len(cm.records), 1)
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(proactive_interval_max=3900, ttl_min=300, ttl_margin=3600)
        self.assertEqual(cfg.proactive_interval_max, 3899)
        cfg = gen(proactive_interval_max=3899, ttl_min=300, ttl_margin=3600)
        self.assertEqual(cfg.proactive_interval_max, 3899)
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(proactive_interval_min=1000, proactive_interval_max=7200, ttl_min=0, ttl_margin=600)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (599, 599))

    def test_ttl_trop_faibles_desactivent(self):
        for ttl_min, ttl_margin in ((0, 0), (30, 0), (0, 60)):
            with self.assertLogs("appalias", level="WARNING") as cm:
                cfg = gen(ttl_min=ttl_min, ttl_margin=ttl_margin)
            self.assertFalse(cfg.proactive_enabled, (ttl_min, ttl_margin))
            self.assertEqual(len(cm.records), 1)
            self.assertGreaterEqual(cfg.proactive_interval_min, 1)

    def test_limite_exacte_plancher(self):
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(ttl_min=1, ttl_margin=60)                           # 61 - 1 = 60 : valide
        self.assertTrue(cfg.proactive_enabled)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (60, 60))
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(ttl_min=0, ttl_margin=60)                           # 59 : désactivé
        self.assertFalse(cfg.proactive_enabled)

    def test_min_above_max(self):
        with self.assertLogs("appalias", level="WARNING") as cm:
            cfg = gen(proactive_interval_min=2000, proactive_interval_max=500)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max), (500, 500))
        self.assertEqual(len(cm.records), 1)

    def test_prefixes(self):
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(proactive_prefixes=["WWW", "api", "bad label", "-x", "a.b", "www", "x" * 64, "cdn_1"])
        self.assertEqual(cfg.proactive_prefixes, ["www", "api", "cdn_1"])
        cfg = gen(proactive_prefixes="www, m ,api")
        self.assertEqual(cfg.proactive_prefixes, ["www", "m", "api"])
        names = ["p%d" % i for i in range(12)]
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(proactive_prefixes=names)
        self.assertEqual(cfg.proactive_prefixes, names[:8])
        self.assertEqual(gen(proactive_prefixes=[]).proactive_prefixes, [])

    def test_app_proactive(self):
        cfg = cfgmod.from_dict({"general": {}, "apps": [
            {"name": "Old"}, {"name": "Off", "proactive": False},
            {"name": "Str", "proactive": "0"}, {"name": "On", "proactive": True}]})
        self.assertEqual([a.proactive for a in cfg.apps], [True, False, False, True])

    def test_old_config_valid(self):
        cfg = cfgmod.from_dict({
            "general": {"enabled": True, "upstream_address": "192.0.2.53", "ttl_min": 300, "ttl_margin": 3600},
            "apps": [{"uuid": "u", "enabled": True, "name": "X", "domains": ["a.example"]}]})
        self.assertTrue(cfg.proactive_enabled)
        self.assertTrue(cfg.apps[0].proactive)
        self.assertEqual((cfg.proactive_interval_min, cfg.proactive_interval_max, cfg.proactive_prefixes),
                         (300, 3600, ["www"]))


if __name__ == "__main__":
    unittest.main()
