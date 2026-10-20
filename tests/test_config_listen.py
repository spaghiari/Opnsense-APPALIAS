"""Relais LAN (0.9) : adresses d'écoute, cache, clients, backend pf, ASN."""

import ipaddress
import unittest

import appalias_testutil  # noqa: F401

from lib import config as cfgmod


def gen(**kw):
    return cfgmod.from_dict({"general": kw})


class ListenTests(unittest.TestCase):
    def test_default(self):
        for cfg in (gen(), cfgmod.from_dict({}), gen(listen_address="")):
            self.assertEqual(cfg.listen_addresses, ["127.0.0.1"])
            self.assertEqual(cfg.listen_address, "127.0.0.1")

    def test_single_loopback(self):
        cfg = gen(listen_address="127.0.0.1")
        self.assertEqual(cfg.listen_addresses, ["127.0.0.1"])
        self.assertEqual(cfg.listen_address, "127.0.0.1")

    def test_csv_prefers_loopback(self):
        cfg = gen(listen_address="192.168.1.1, 127.0.0.1")
        self.assertEqual(cfg.listen_addresses, ["192.168.1.1", "127.0.0.1"])
        self.assertEqual(cfg.listen_address, "127.0.0.1")

    def test_list_input(self):
        cfg = gen(listen_address=["10.0.0.1", "::1"])
        self.assertEqual(cfg.listen_addresses, ["10.0.0.1", "::1"])
        self.assertEqual(cfg.listen_address, "::1")

    def test_single_non_loopback(self):
        cfg = gen(listen_address="192.168.1.1")
        self.assertEqual(cfg.listen_addresses, ["192.168.1.1"])
        self.assertEqual(cfg.listen_address, "192.168.1.1")

    def test_duplicates_removed(self):
        cfg = gen(listen_address="192.168.1.1,192.168.1.1,127.0.0.1,127.0.0.1")
        self.assertEqual(cfg.listen_addresses, ["192.168.1.1", "127.0.0.1"])

    def test_invalid(self):
        for bad in ("any", "x", "192.168.1.1,any", "127.0.0.1,x"):
            with self.assertRaises(cfgmod.ConfigError, msg=bad):
                gen(listen_address=bad)

    def test_direct_construction(self):
        cfg = cfgmod.Config(True, "127.0.0.1", 53535, [], True, 300, 3600, 10, [], "APP_", "info", [])
        self.assertEqual(cfg.listen_addresses, ["127.0.0.1"])


class RelayOptionsTests(unittest.TestCase):
    def test_cache_enabled(self):
        self.assertTrue(gen().cache_enabled)
        self.assertTrue(gen(cache_enabled="1").cache_enabled)
        self.assertFalse(gen(cache_enabled="0").cache_enabled)
        self.assertFalse(gen(cache_enabled=False).cache_enabled)

    def test_client_networks(self):
        self.assertEqual(gen().client_networks, [])
        with self.assertLogs("appalias", level="WARNING"):
            cfg = gen(client_networks="192.168.0.0/16, 10.0.0.0/8,bad")
        self.assertEqual(cfg.client_networks,
                         [ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("10.0.0.0/8")])
        with self.assertRaises(cfgmod.ConfigError):
            gen(client_networks="any")

    def test_client_rate_limit(self):
        self.assertEqual(gen().client_rate_limit, 100)
        self.assertEqual(gen(client_rate_limit="0").client_rate_limit, 0)
        self.assertEqual(gen(client_rate_limit=250).client_rate_limit, 250)
        self.assertEqual(gen(client_rate_limit=-5).client_rate_limit, 0)
        self.assertEqual(gen(client_rate_limit=10 ** 9).client_rate_limit, 100000)
        self.assertEqual(gen(client_rate_limit="abc").client_rate_limit, 100)

    def test_pf_backend(self):
        self.assertEqual(gen().pf_backend, "auto")
        for value in ("auto", "pfctl", "ioctl"):
            self.assertEqual(gen(pf_backend=value).pf_backend, value)
        self.assertEqual(gen(pf_backend=" IOCTL ").pf_backend, "ioctl")
        with self.assertLogs("appalias", level="WARNING") as cm:
            cfg = gen(pf_backend="bogus")
        self.assertEqual(cfg.pf_backend, "auto")
        self.assertEqual(len(cm.records), 1)

    def test_asn_enabled(self):
        self.assertTrue(gen().asn_enabled)
        self.assertFalse(gen(asn_enabled="0").asn_enabled)
        self.assertTrue(gen(asn_enabled="1").asn_enabled)


if __name__ == "__main__":
    unittest.main()
