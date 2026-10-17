"""Réglages DNS over TLS de config.json et syntaxe des amonts de secours."""

import unittest

import appalias_testutil  # noqa: F401  (sys.path)

from lib import config as cfgmod
from lib.config import Upstream


def load(**general):
    gen = {"enabled": True, "upstream_address": "9.9.9.9"}
    gen.update(general)
    return cfgmod.from_dict({"general": gen, "apps": []})


class ProtocolTests(unittest.TestCase):
    def test_default_plain_dns(self):
        cfg = load()
        self.assertEqual(cfg.upstream_protocol, "dns")
        self.assertEqual(cfg.upstreams, [Upstream("9.9.9.9", 53, None)])

    def test_dot_quad9(self):
        cfg = load(upstream_protocol="dot", upstream_port=853, upstream_tls_name="DNS.Quad9.net.",
                   upstream_fallback=["149.112.112.112#dns.quad9.net"])
        self.assertEqual(cfg.upstream_protocol, "dot")
        self.assertEqual(cfg.upstream_tls_name, "dns.quad9.net")
        self.assertEqual(cfg.upstreams, [Upstream("9.9.9.9", 853, "dns.quad9.net"),
                                         Upstream("149.112.112.112", 853, "dns.quad9.net")])

    def test_dot_port_53_kept(self):
        # pas de changement silencieux du port enregistré
        cfg = load(upstream_protocol="dot", upstream_port=53, upstream_tls_name="dns.quad9.net")
        self.assertEqual(cfg.upstreams[0].port, 53)

    def test_dot_port_default_853(self):
        cfg = load(upstream_protocol="dot", upstream_tls_name="dns.quad9.net")
        self.assertEqual(cfg.upstreams[0].port, 853)

    def test_dot_requires_tls_name(self):
        for name in ("", None, "  "):
            with self.assertRaises(cfgmod.ConfigError) as ctx:
                load(upstream_protocol="dot", upstream_port=853, upstream_tls_name=name)
            self.assertIn("upstream_tls_name", str(ctx.exception))

    def test_dot_fallback_requires_name(self):
        # service désactivé, sans amont principal ni nom : le secours n'a pas de nom
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.from_dict({"general": {"enabled": False, "upstream_protocol": "dot",
                                          "upstream_fallback": ["192.0.2.1"]}})

    def test_invalid_protocol_refused(self):
        for value in ("doh", "tls", "DoT2"):
            with self.assertRaises(cfgmod.ConfigError):
                load(upstream_protocol=value)

    def test_invalid_tls_name_refused(self):
        for name in ("bad name", "-x.example", "a..b", "x#y"):
            with self.assertRaises(cfgmod.ConfigError):
                load(upstream_protocol="dot", upstream_tls_name=name)

    def test_tls_name_ignored_in_plain_dns(self):
        cfg = load(upstream_tls_name="dns.quad9.net", upstream_fallback=["149.112.112.112#dns.quad9.net"])
        self.assertEqual([u.tls_name for u in cfg.upstreams], [None, None])


class FallbackSyntaxTests(unittest.TestCase):
    def parse(self, items, protocol="dns", name=None):
        gen = {"upstream_protocol": protocol, "upstream_tls_name": name, "upstream_fallback": items}
        if protocol == "dot" and name is None:
            gen["upstream_tls_name"] = "main.example"
        return load(**gen).upstreams[1:]

    def test_without_port(self):
        # corrigé en 0.4 : « 149.112.112.112 » sans port était refusé
        self.assertEqual(self.parse(["149.112.112.112"]), [Upstream("149.112.112.112", 53, None)])
        self.assertEqual(self.parse(["149.112.112.112"], "dot"),
                         [Upstream("149.112.112.112", 853, "main.example")])

    def test_with_port(self):
        self.assertEqual(self.parse(["192.0.2.1:5353"]), [Upstream("192.0.2.1", 5353, None)])
        self.assertEqual(self.parse(["192.0.2.1:8853"], "dot"),
                         [Upstream("192.0.2.1", 8853, "main.example")])

    def test_ipv6(self):
        self.assertEqual(self.parse(["[2620:fe::fe]", "[2620:fe::9]:5353", "2620:fe::10"]),
                         [Upstream("2620:fe::fe", 53, None), Upstream("2620:fe::9", 5353, None),
                          Upstream("2620:fe::10", 53, None)])
        self.assertEqual(self.parse(["[2620:fe::fe]#dns.quad9.net", "[2620:fe::9]:853#other.example"], "dot"),
                         [Upstream("2620:fe::fe", 853, "dns.quad9.net"),
                          Upstream("2620:fe::9", 853, "other.example")])

    def test_tls_name_suffix(self):
        self.assertEqual(self.parse(["149.112.112.112#dns.quad9.net", "192.0.2.1:853#x.example",
                                     "192.0.2.2"], "dot"),
                         [Upstream("149.112.112.112", 853, "dns.quad9.net"),
                          Upstream("192.0.2.1", 853, "x.example"),
                          Upstream("192.0.2.2", 853, "main.example")])

    def test_invalid(self):
        for item in ("192.0.2.1:", "192.0.2.1:0", "192.0.2.1:65536", "[2001:db8::1]:", "[2001:db8::1",
                     "[2001:db8::1]x", "host.example", "192.0.2.1#", "192.0.2.1#bad name",
                     "192.0.2.1:abc", "any"):
            with self.assertRaises(cfgmod.ConfigError, msg=item):
                self.parse([item], "dot")

    def test_strings_like_mvc(self):
        cfg = load(upstream_protocol="dot", upstream_tls_name="dns.quad9.net", upstream_port="853",
                   upstream_fallback="149.112.112.112,[2620:fe::fe]:853#dns.quad9.net")
        self.assertEqual(len(cfg.upstreams), 3)
        self.assertTrue(all(u.port == 853 and u.tls_name == "dns.quad9.net" for u in cfg.upstreams))


if __name__ == "__main__":
    unittest.main()
