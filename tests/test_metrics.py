"""Mesures de latence : fenêtre glissante, fichier metrics.json, status.py."""

import json
import os
import tempfile
import unittest

from appalias_testutil import FakePf

import appaliasd
import status
from lib import config as cfgmod
from lib import metrics
from lib.pftable import PfTable


class WindowTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(metrics.Window().summary(),
                         {"count": 0, "window": 0, "avg_ms": None, "p95_ms": None})

    def test_avg_p95(self):
        win = metrics.Window()
        for ms in range(1, 101):        # 1..100 ms
            win.add(ms)
        s = win.summary()
        self.assertEqual((s["count"], s["window"]), (100, 100))
        self.assertEqual(s["avg_ms"], 50.5)
        self.assertEqual(s["p95_ms"], 95.0)

    def test_bounded_window(self):
        win = metrics.Window(size=256)
        for _ in range(300):
            win.add(1000)
        for _ in range(256):
            win.add(2)
        s = win.summary()
        self.assertEqual((s["count"], s["window"]), (556, 256))
        self.assertEqual((s["avg_ms"], s["p95_ms"]), (2.0, 2.0))   # anciennes mesures sorties
        self.assertEqual(len(win.values), 256)

    def test_single(self):
        win = metrics.Window()
        win.add(7.25)
        self.assertEqual(win.summary()["p95_ms"], 7.2)

    def test_metrics_retain(self):
        m = metrics.Metrics()
        m.add_upstream("9.9.9.9@853", 20)
        m.add_upstream("192.0.2.1@53", 5)
        m.add_pf(3)
        m.retain({"9.9.9.9@853"})
        d = m.to_dict()
        self.assertEqual(list(d["upstreams"]), ["9.9.9.9@853"])
        self.assertEqual(d["pf"]["count"], 1)
        self.assertEqual(d["window"], 256)


class RelayCountersTests(unittest.TestCase):
    def test_defaults_in_to_dict(self):
        out = metrics.Metrics().to_dict()
        self.assertEqual(out["cache"], {"hits": 0, "misses": 0, "entries": 0})
        self.assertEqual(out["clients"], {"rate_limited": 0, "refused": 0})
        self.assertNotIn("asn", out)

    def test_cache(self):
        m = metrics.Metrics()
        m.add_cache(True)
        m.add_cache(True)
        m.add_cache(False)
        self.assertEqual((m.cache["hits"], m.cache["misses"]), (2, 1))
        self.assertEqual(m.version, 3)
        m.set_cache_entries(42)
        self.assertEqual(m.to_dict()["cache"], {"hits": 2, "misses": 1, "entries": 42})
        self.assertEqual(m.version, 4)
        m.set_cache_entries(42)            # inchangé : pas d'écriture inutile
        self.assertEqual(m.version, 4)

    def test_clients(self):
        m = metrics.Metrics()
        m.add_client("rate_limited")
        m.add_client("refused", 3)
        m.add_client("inconnu")
        m.add_client("refused", 0)
        self.assertEqual(m.to_dict()["clients"], {"rate_limited": 1, "refused": 3})
        self.assertEqual(m.version, 2)

    def test_asn(self):
        m = metrics.Metrics()
        m.set_asn(0, 0)
        self.assertEqual(m.version, 0)
        self.assertNotIn("asn", m.to_dict())
        m.set_asn(2, 150)
        self.assertEqual(m.to_dict()["asn"], {"tables": 2, "prefixes": 150})
        self.assertEqual(m.version, 1)
        m.set_asn(2, 150)
        self.assertEqual(m.version, 1)
        m.set_asn(0, 0)                    # retombe à zéro : section masquée
        self.assertNotIn("asn", m.to_dict())

    def test_to_dict_copies(self):
        m = metrics.Metrics()
        out = m.to_dict()
        out["cache"]["hits"] = 99
        out["clients"]["refused"] = 99
        self.assertEqual(m.cache["hits"], 0)
        self.assertEqual(m.clients["refused"], 0)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.config = os.path.join(self.tmp.name, "config.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"general": {"enabled": False, "upstream_address": "192.0.2.53"},
                       "apps": [{"uuid": "u1", "name": "TEST", "domains": ["example.com"]}]}, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def relay(self):
        cfg = cfgmod.load(self.config)
        return appaliasd.Relay(cfg, PfTable(pfctl=["pfctl"], runner=FakePf(), tmpdir=self.tmp.name),
                               state_path=self.state, config_path=self.config)

    def test_path_next_to_state(self):
        self.assertEqual(metrics.path_for(self.state), os.path.join(self.tmp.name, "metrics.json"))
        self.assertEqual(self.relay().metrics_path, os.path.join(self.tmp.name, "metrics.json"))

    def test_save_only_when_changed(self):
        relay = self.relay()
        path = relay.metrics_path
        relay.metrics.add_upstream("192.0.2.53@53", 12)
        relay.save_metrics()
        first = os.stat(path).st_mtime_ns
        os.utime(path, ns=(0, 0))
        relay.save_metrics()                         # rien de neuf : pas d'écriture
        self.assertEqual(os.stat(path).st_mtime_ns, 0)
        relay.metrics.add_pf(4)
        relay.save_metrics()
        self.assertNotEqual(os.stat(path).st_mtime_ns, 0)
        self.assertTrue(first)

    def test_status_includes_latency(self):
        relay = self.relay()
        for ms in (10, 20, 30):
            relay.metrics.add_upstream("192.0.2.53@53", ms)
        relay.metrics.add_pf(5)
        relay.save_metrics()
        out = status.status(self.config, self.state, PfTable(pfctl=["pfctl"], runner=FakePf()))
        lat = out["latency"]
        self.assertEqual(lat["upstreams"]["192.0.2.53@53"],
                         {"count": 3, "window": 3, "avg_ms": 20.0, "p95_ms": 30.0})
        self.assertEqual(lat["pf"]["avg_ms"], 5.0)
        self.assertIn("time", lat)

    def test_status_without_metrics(self):
        out = status.status(self.config, self.state, PfTable(pfctl=["pfctl"], runner=FakePf()))
        self.assertEqual(out["latency"], {})


if __name__ == "__main__":
    unittest.main()
