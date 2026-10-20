import json
import os
import tempfile
import time
import unittest

from appalias_testutil import FakePf

import status
from lib import metrics
from lib import proactive
from lib.pftable import PfTable
from lib.state import State


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.state = os.path.join(self.tmp.name, "state.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"general": {"enabled": False, "upstream_address": "192.0.2.53"},
                       "apps": [{"uuid": "u1", "name": "TEST", "domains": ["example.com"]}]}, fh)
        st = State()
        st.hit("APP_TEST", 42)
        st.error("APP_TEST", 43, "boom")
        st.save(self.state)
        self.fake = FakePf()
        self.fake.tables["APP_TEST"] = {"192.0.2.1", "192.0.2.2", "198.51.100.0/24", "2001:db8::1"}
        self.pf = PfTable(pfctl=["pfctl"], runner=self.fake)

    def tearDown(self):
        self.tmp.cleanup()

    def test_status(self):
        out = status.status(self.config, self.state, self.pf)
        self.assertEqual(out["status"], "disabled")
        app = out["apps"]["u1"]
        self.assertEqual((app["v4"], app["v6"]), (3, 1))
        self.assertEqual(app["last_hit"], 42.0)
        self.assertEqual(app["last_error"]["message"], "boom")

    def test_show_validates(self):
        self.assertIn("error", status.show("bad;name", self.config, self.pf))
        self.assertIn("error", status.show("APP_OTHER", self.config, self.pf))
        self.assertEqual(len(status.show("APP_TEST", self.config, self.pf)["entries"]), 4)
        self.assertEqual(self.fake.ops("show", "APP_OTHER"), [])

    def test_missing_config(self):
        out = status.status(os.path.join(self.tmp.name, "absent.json"), self.state, self.pf)
        self.assertEqual(out["status"], "stopped")
        self.assertTrue(out["error"])


class StatusWarningsAsnTests(unittest.TestCase):
    """0.9 : avertissements (port Unbound, repli du backend pf) et sources ASN."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.config = os.path.join(d, "config.json")
        self.state = os.path.join(d, "state.json")
        self.child = os.path.join(d, "child.pid")
        self.asn = os.path.join(d, "asn.json")
        with open(self.child, "w", encoding="ascii") as fh:
            fh.write("4242\n")
        self.pf = PfTable(pfctl=["pfctl"], runner=FakePf())
        self.write_config()

    def tearDown(self):
        self.tmp.cleanup()

    def write_config(self, unbound=None, asn_enabled=True):
        data = {"general": {"enabled": True, "upstream_address": "192.0.2.53",
                            "listen_port": 53535, "asn_enabled": asn_enabled},
                "apps": [{"uuid": "u1", "name": "NF", "domains": ["example.com"],
                          "asns": ["AS2906", "as40027"]},
                         {"uuid": "u2", "name": "OTHER", "domains": ["example.org"]}]}
        if unbound is not None:
            data["unbound"] = unbound
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def run_status(self, running=False):
        orig = status.daemon_running
        if running:
            status.daemon_running = lambda: 1
        try:
            return status.status(self.config, self.state, self.pf, child_pidfile=self.child,
                                 asn_cache_path=self.asn)
        finally:
            status.daemon_running = orig

    def test_warnings_vides(self):
        out = self.run_status()
        self.assertEqual(out["warnings"], [])

    def test_conflit_port_unbound(self):
        self.write_config(unbound={"enabled": True, "port": 53535})
        self.assertEqual(self.run_status()["warnings"],
                         [{"code": "unbound_port_conflict", "port": 53535}])
        self.write_config(unbound={"enabled": False, "port": 53535})
        self.assertEqual(self.run_status()["warnings"], [])
        self.write_config(unbound={"enabled": True, "port": 53})
        self.assertEqual(self.run_status()["warnings"], [])

    def test_repli_backend_pf(self):
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 4242, "pf_backend": "pfctl",
                                  "pf_backend_reason": "pas de /dev/pf"}})
        self.assertEqual(self.run_status(running=True)["warnings"],
                         [{"code": "pf_backend_fallback", "reason": "pas de /dev/pf"}])
        self.assertEqual(self.run_status(running=False)["warnings"], [])   # démon arrêté
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 999, "pf_backend_reason": "pas de /dev/pf"}})
        self.assertEqual(self.run_status(running=True)["warnings"], [])    # autre processus
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 4242, "pf_backend": "ioctl", "pf_backend_reason": None}})
        self.assertEqual(self.run_status(running=True)["warnings"], [])

    def test_asn(self):
        with open(self.asn, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "asns": {
                "2906": {"prefixes": ["9.9.9.0/24", "2620:fe::/32"], "fetched": 1000,
                         "error": None},
                "40027": {"prefixes": ["8.8.8.0/24"], "fetched": 2000,
                          "error": "délai dépassé", "attempted": 5000},
                "64500": {"prefixes": ["1.1.1.0/24"], "fetched": 3000, "error": None}}}, fh)
        out = self.run_status()
        self.assertEqual(out["asn"], {"enabled": True, "asns": 2, "prefixes": 3, "fetched": 2000,
                                      "errors": {"40027": "délai dépassé"}})
        self.assertEqual(out["apps"]["u1"]["asn_prefixes"], 3)
        self.assertEqual(out["apps"]["u2"]["asn_prefixes"], 0)
        self.write_config(asn_enabled=False)
        out = self.run_status()
        self.assertFalse(out["asn"]["enabled"])
        self.assertEqual(out["apps"]["u1"]["asn_prefixes"], 0)

    def test_asn_erreur_tronquee(self):
        with open(self.asn, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "asns": {
                "2906": {"prefixes": [], "fetched": None, "error": "x" * 1000}}}, fh)
        self.assertEqual(len(self.run_status()["asn"]["errors"]["2906"]), status.MAX_TEXT)

    def test_redemarrage_backend_pf(self):
        self.write_config()   # pf_backend « auto » par défaut
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 4242, "pf_backend": "pfctl", "pf_backend_mode": "pfctl"}})
        self.assertEqual(self.run_status(running=True)["warnings"],
                         [{"code": "pf_backend_restart", "wanted": "auto"}])
        self.assertEqual(self.run_status(running=False)["warnings"], [])
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 4242, "pf_backend": "ioctl", "pf_backend_mode": "auto"}})
        self.assertEqual(self.run_status(running=True)["warnings"], [])

    def test_champs_daemon_verifies(self):
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 4242, "pf_backend": "ioctl", "pf_backend_mode": "auto",
                                  "pf_backend_reason": None}})
        daemon = self.run_status(running=True)["latency"]["daemon"]
        self.assertEqual((daemon["pf_backend"], daemon["pf_backend_mode"]), ("ioctl", "auto"))
        daemon = self.run_status(running=False)["latency"]["daemon"]   # démon arrêté
        self.assertIsNone(daemon["pf_backend"])
        metrics.write(metrics.path_for(self.state),
                      {"daemon": {"pid": 999, "pf_backend": "ioctl"}})
        self.assertIsNone(self.run_status(running=True)["latency"]["daemon"]["pf_backend"])

    def test_asn_cache_absent(self):
        out = self.run_status()
        self.assertEqual(out["asn"], {"enabled": True, "asns": 0, "prefixes": 0, "fetched": None,
                                      "errors": {}})
        self.assertEqual(out["apps"]["u1"]["asn_prefixes"], 0)


class StatusDiscoveryTests(unittest.TestCase):
    """0.10 : probe_names, seen_names et discovery."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.state = os.path.join(self.tmp.name, "state.json")
        self.pf = PfTable(pfctl=["pfctl"], runner=FakePf())

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, general=None, wildcard=False):
        gen = {"enabled": False, "upstream_address": "192.0.2.53"}
        gen.update(general or {})
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"general": gen, "apps": [
                {"uuid": "u1", "name": "SIG", "domains": ["domain:signal.org"],
                 "catalog_refs": ["signal"]},
                {"uuid": "u2", "name": "OFF", "domains": ["domain:example.org"],
                 "enabled": False}]}, fh)
        st = State()
        now = time.time()
        st.see("APP_SIG", "chat.signal.org", now)
        st.see("APP_SIG", "sous.domaine.signal.org", now)
        if wildcard:
            st.set_wildcard("signal.org", now)
        st.set_wildcard("vieux.example", now - 8 * 86400)
        st.save(self.state)

    def test_avec_dictionnaire(self):
        self.write()
        out = status.status(self.config, self.state, self.pf)
        labels = proactive.load_labels()
        hints = [n for n in proactive.load_hints()["signal"] if n.endswith(".signal.org")]
        expected = {"signal.org", "www.signal.org", "sous.domaine.signal.org"}
        expected |= set(hints) | {lab + ".signal.org" for lab in labels}
        app = out["apps"]["u1"]
        self.assertEqual(app["probe_names"], len(expected))
        self.assertEqual(app["seen_names"], 2)
        self.assertEqual(out["apps"]["u2"]["probe_names"], 0)
        self.assertEqual(out["apps"]["u2"]["seen_names"], 0)
        self.assertEqual(out["discovery"], {"deep": True, "labels": len(labels),
                                            "targets": len(expected), "wildcards": []})

    def test_joker_et_sans_dictionnaire(self):
        self.write(wildcard=True)
        out = status.status(self.config, self.state, self.pf)
        self.assertEqual(out["discovery"]["wildcards"], ["signal.org"])
        with_wild = out["apps"]["u1"]["probe_names"]
        self.write(general={"proactive_deep": "0"})
        out = status.status(self.config, self.state, self.pf)
        self.assertFalse(out["discovery"]["deep"])
        # sans joker, le nom vu « sous.domaine.signal.org » redevient une cible
        self.assertEqual(out["apps"]["u1"]["probe_names"], with_wild + 1)
        self.write(general={"proactive_enabled": "0"})
        out = status.status(self.config, self.state, self.pf)
        self.assertEqual(out["apps"]["u1"]["probe_names"], 0)
        self.assertEqual(out["discovery"]["targets"], 0)
        self.assertEqual(out["apps"]["u1"]["seen_names"], 2)


if __name__ == "__main__":
    unittest.main()
