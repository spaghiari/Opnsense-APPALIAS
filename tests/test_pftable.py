import os
import sys
import tempfile
import unittest
from unittest import mock

from appalias_testutil import FakePf

from lib import config as cfgmod
from lib.pftable import PfError, PfTable, normalize_entry, pfctl_argv, valid_table

FAKE_PFCTL = r'''
import sys
log = sys.argv[1]
with open(log, "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[2:]) + "\n")
if "show" in sys.argv:
    print("   192.0.2.1")
    print("   2001:db8::1")
if "fail" in sys.argv:
    sys.stderr.write("pfctl: erreur simulée\n")
    sys.exit(1)
'''


class PfTableTests(unittest.TestCase):
    def test_table_names(self):
        self.assertTrue(valid_table("APP_TEST_1"))
        for bad in ("", "a" * 32, "APP-X", "APP X", "APP;rm", "../x", None, "é"):
            self.assertFalse(valid_table(bad))
        pf = PfTable(pfctl=["pfctl"], runner=FakePf())
        with self.assertRaises(PfError):
            pf.add("bad name", ["192.0.2.1"])

    def test_entries_validated(self):
        pf = PfTable(pfctl=["pfctl"], runner=FakePf())
        with self.assertRaises(ValueError):
            pf.add("APP_A", ["-F all"])
        self.assertEqual(normalize_entry("192.0.2.1/32"), "192.0.2.1")
        self.assertEqual(normalize_entry("192.0.2.9/24"), "192.0.2.0/24")

    def test_argv_shape(self):
        fake = FakePf()
        pf = PfTable(pfctl=["pfctl"], runner=fake)
        pf.add("APP_A", ["192.0.2.1", "2001:db8::1"])
        pf.add("APP_A", [])
        self.assertEqual(fake.calls, [("add", "APP_A", ["192.0.2.1", "2001:db8::1"])])
        with tempfile.TemporaryDirectory() as tmp:
            pf = PfTable(pfctl=["pfctl"], runner=fake, tmpdir=tmp)
            pf.replace("APP_A", ["198.51.100.0/24"])
            self.assertEqual(os.listdir(tmp), [])
        self.assertEqual(fake.contents("APP_A"), {"198.51.100.0/24"})

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"APPALIAS_PFCTL": ""}):
            self.assertEqual(pfctl_argv(), ["/sbin/pfctl"])
        with mock.patch.dict(os.environ, {"APPALIAS_PFCTL": '"/usr/bin/python3" fake.py'}):
            self.assertEqual(pfctl_argv(), ["/usr/bin/python3", "fake.py"])

    def test_real_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "fake_pfctl.py")
            logf = os.path.join(tmp, "calls.log")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(FAKE_PFCTL)
            env = '"%s" -I "%s" "%s"' % (sys.executable.replace("\\", "/"),
                                         script.replace("\\", "/"), logf.replace("\\", "/"))
            with mock.patch.dict(os.environ, {"APPALIAS_PFCTL": env}):
                pf = PfTable(timeout=20)
            pf.add("APP_A", ["192.0.2.1"])
            self.assertEqual(pf.show("APP_A"), ["192.0.2.1", "2001:db8::1"])
            with self.assertRaises(PfError):
                pf._run(["-t", "APP_A", "-T", "fail"])
            with open(logf, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(lines[0], "-t APP_A -T add 192.0.2.1")


class ConfigTests(unittest.TestCase):
    def test_upstream_required(self):
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.from_dict({"general": {"upstream_address": ""}})

    def test_wildcard_any_rejected(self):
        base = {"upstream_address": "192.0.2.53"}
        cases = [
            {"general": dict(base, listen_address="any")},
            {"general": dict(base, upstream_address="any")},
            {"general": dict(base, upstream_fallback="any")},
            {"general": dict(base, exclude_networks="10.0.0.0/8,any")},
            {"general": base, "apps": [{"name": "X", "networks": "any"}]},
        ]
        for data in cases:
            with self.assertRaises(cfgmod.ConfigError) as cm:
                cfgmod.from_dict(data)
            self.assertIn("any", str(cm.exception))

    def test_listen_port_range(self):
        for port in (0, "0", 65536, -1):
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.from_dict({"general": {"upstream_address": "192.0.2.53", "listen_port": port}})
        cfg = cfgmod.from_dict({"general": {"upstream_address": "192.0.2.53", "listen_port": "1"}})
        self.assertEqual(cfg.listen_port, 1)

    def test_default_timeout(self):
        self.assertEqual(PfTable(pfctl=["pfctl"]).timeout, 3.0)

    def test_strings_like_mvc(self):
        cfg = cfgmod.from_dict({
            "general": {"enabled": "1", "upstream_address": "192.0.2.53", "upstream_port": "53",
                        "upstream_fallback": "198.51.100.53:5353,[2001:db8::53]:53",
                        "follow_cname": "0", "exclude_networks": "10.0.0.0/8,fe80::/10",
                        "loglevel": "debug"},
            "apps": [{"name": "X", "domains": "example.com,full:a.example.net",
                      "networks": "192.0.2.0/24\n2001:db8::/32", "enabled": "1"},
                     {"name": "bad name"}, {"name": "X"}],
        })
        self.assertTrue(cfg.enabled)
        self.assertFalse(cfg.follow_cname)
        self.assertEqual(cfg.upstreams, [("192.0.2.53", 53), ("198.51.100.53", 5353),
                                         ("2001:db8::53", 53)])
        self.assertEqual(len(cfg.apps), 1)
        self.assertEqual(cfg.apps[0].table, "APP_X")
        self.assertEqual(cfg.apps[0].domains, ["example.com", "full:a.example.net"])
        self.assertEqual(len(cfg.apps[0].networks), 2)
        self.assertEqual(cfg.listen_port, 53535)
        self.assertEqual(cfg.ttl_min, 300)


if __name__ == "__main__":
    unittest.main()
