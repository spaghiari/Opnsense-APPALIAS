"""0.5 : version du démon dans l'état, réglages de découverte, option en ligne."""

import json
import os
import tempfile
import unittest

import appalias_testutil  # noqa: F401
from appalias_testutil import FakePf

import catalog_update as upd
import status
from lib import config as cfgmod
from lib import metrics
from lib.pftable import PfTable
from lib.version import VERSION


class StatusVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.config = os.path.join(d, "config.json")
        self.state = os.path.join(d, "state.json")
        self.child = os.path.join(d, "child.pid")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"general": {"enabled": True, "upstream_address": "192.0.2.53"}, "apps": []}, fh)
        with open(self.child, "w", encoding="ascii") as fh:
            fh.write("4242\n")
        self.pf = PfTable(pfctl=["pfctl"], runner=FakePf())

    def tearDown(self):
        self.tmp.cleanup()

    def run_status(self, daemon):
        metrics.write(metrics.path_for(self.state), {"daemon": daemon})
        orig = status.daemon_running
        status.daemon_running = lambda: 1
        try:
            return status.status(self.config, self.state, self.pf, child_pidfile=self.child)
        finally:
            status.daemon_running = orig

    def test_versions(self):
        out = self.run_status({"version": VERSION, "pid": 4242})
        self.assertEqual((out["version"], out["installed_version"], out["restart_needed"]),
                         (VERSION, VERSION, False))
        out = self.run_status({"version": "0.4.2", "pid": 4242})
        self.assertTrue(out["restart_needed"])
        out = self.run_status({"version": "0.4.2", "pid": 999})     # autre processus : inconnu
        self.assertIsNone(out["version"])
        self.assertIsNone(out["restart_needed"])


class ConfigTests(unittest.TestCase):
    def test_legacy_discovery_keys_tolerated(self):
        # Les clés de l'ancienne fonction Discovery restent tolérées en lecture
        # (config.json d'un export plus ancien) : ignorées, sans erreur.
        cfg = cfgmod.from_dict({"general": {
            "discovery_enabled": True, "discovery_retention_days": 7, "discovery_max_names": 5000,
            "discovery_window": 5, "discovery_ignored": ["full:a.example", "domain:b.example"]}})
        self.assertFalse(hasattr(cfg, "discovery_enabled"))
        self.assertFalse(hasattr(cfg, "discovery_ignored"))


class OnlineOptionTests(unittest.TestCase):
    def test_online_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            self.assertTrue(upd.online_enabled(path))                  # absent : défaut actif
            for value, expected in ((True, True), (False, False), ("0", False), ("1", True)):
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"general": {"catalog_online_update": value}}, fh)
                self.assertEqual(upd.online_enabled(path), expected, value)


if __name__ == "__main__":
    unittest.main()
