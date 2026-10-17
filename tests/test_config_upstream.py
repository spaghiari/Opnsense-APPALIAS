import unittest

import appalias_testutil  # noqa: F401  (sys.path)

from lib import config as cfgmod


def make(enabled, upstream, **extra):
    gen = {"enabled": enabled, "upstream_address": upstream}
    gen.update(extra)
    return {"general": gen, "apps": []}


class UpstreamVideTests(unittest.TestCase):
    def test_desactive_amont_vide_accepte(self):
        for value in ("", None, "   "):
            cfg = cfgmod.from_dict(make(False, value))
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.upstreams, [])

    def test_desactive_amont_vide_avec_secours(self):
        cfg = cfgmod.from_dict(make(False, "", upstream_fallback=["192.0.2.1:53"]))
        self.assertEqual(cfg.upstreams, [("192.0.2.1", 53, None)])

    def test_active_amont_vide_leve_configerror(self):
        for value in ("", None, "  "):
            with self.assertRaises(cfgmod.ConfigError) as ctx:
                cfgmod.from_dict(make(True, value))
            self.assertIn("upstream_address", str(ctx.exception))

    def test_active_amont_renseigne(self):
        cfg = cfgmod.from_dict(make(True, "192.0.2.53", upstream_port=5353))
        self.assertEqual(cfg.upstreams, [("192.0.2.53", 5353, None)])

    def test_code_de_sortie_78_si_active_sans_amont(self):
        import json
        import os
        import tempfile
        import appaliasd
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(make(True, ""), fh)
            rc = appaliasd.main(["--config", path, "--state", os.path.join(tmp, "s.json"), "--stderr"])
        self.assertEqual(rc, 78)


if __name__ == "__main__":
    unittest.main()
