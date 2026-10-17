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
