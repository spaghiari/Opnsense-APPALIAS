"""0.9 : accès des clients au relais (réseaux autorisés, débit) et backend pf.

Ces tests construisent le relais sans réseau (comme tests/test_races.py).
"""

import asyncio
import errno
import os
import time
import unittest

from appalias_testutil import FakePf
from test_pfioctl import FakePfDev

import appaliasd
from lib import config as cfgmod
from lib import pfioctl as pio
from lib.pftable import PfTable, make_backend

NO_ASN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "absent", "asn.json")


def make_cfg(**general):
    gen = {"upstream_address": "127.0.0.1"}
    gen.update(general)
    return cfgmod.from_dict({"general": gen,
                             "apps": [{"name": "T", "domains": ["example.com"]}]})


def make_relay(**general):
    return appaliasd.Relay(make_cfg(**general), PfTable(pfctl=["pfctl"], runner=FakePf()),
                           clock=time.time, asn_cache_path=NO_ASN)


class FakeReader:
    async def readexactly(self, n):
        raise AssertionError("connexion refusée : rien ne doit être lu")


class FakeWriter:
    def __init__(self, peer):
        self.peer = peer
        self.closed = False

    def get_extra_info(self, key):
        return self.peer if key == "peername" else None

    def close(self):
        self.closed = True


class ClientAccessTests(unittest.TestCase):
    def test_acl_defaut(self):
        relay = make_relay()
        self.assertFalse(relay.accept_client("8.8.8.8"))
        self.assertTrue(relay.accept_client("192.168.1.10"))
        self.assertTrue(relay.accept_client("127.0.0.1"))
        self.assertEqual(relay.metrics.to_dict()["clients"]["refused"], 1)

    def test_debit(self):
        relay = make_relay(client_rate_limit=2)
        results = [relay.accept_client("192.168.1.10") for _ in range(5)]
        self.assertEqual(results, [True] * 4 + [False])   # rafale de 2 x débit
        self.assertEqual(relay.metrics.to_dict()["clients"]["rate_limited"], 1)
        for _ in range(50):
            self.assertTrue(relay.accept_client("127.0.0.1"))   # bouclage jamais limité

    def test_limiteur_conserve_si_debit_inchange(self):
        relay = make_relay(client_rate_limit=2)
        limiter = relay.limiter
        relay._apply_config(make_cfg(client_rate_limit=2))
        self.assertIs(relay.limiter, limiter)
        relay._apply_config(make_cfg(client_rate_limit=5))
        self.assertIsNot(relay.limiter, limiter)

    def test_reseaux_explicites(self):
        relay = make_relay()
        relay._apply_config(make_cfg(client_networks=["10.0.0.0/8"]))
        self.assertFalse(relay.accept_client("192.168.1.10"))
        self.assertTrue(relay.accept_client("10.1.2.3"))

    def test_tcp_refuse_hors_reseau(self):
        relay = make_relay()
        writer = FakeWriter(("8.8.8.8", 40000))
        asyncio.run(relay._tcp_client(FakeReader(), writer))
        self.assertTrue(writer.closed)
        self.assertEqual(relay._tcp_clients, 0)
        self.assertEqual(relay.metrics.to_dict()["clients"]["refused"], 1)

    def test_tcp_refuse_peer_inconnu(self):
        relay = make_relay()
        for peer in (None, (), ("", 0), (None, 0)):
            writer = FakeWriter(peer)
            asyncio.run(relay._tcp_client(FakeReader(), writer))
            self.assertTrue(writer.closed)
        self.assertEqual(relay.metrics.to_dict()["clients"]["refused"], 4)
        self.assertEqual(relay._tcp_clients, 0)

    def test_adresses_ecoute_jamais_limitees(self):
        relay = make_relay(client_rate_limit=2, listen_address="192.168.1.1")
        for _ in range(20):
            self.assertTrue(relay.accept_client("192.168.1.1"))
        results = [relay.accept_client("192.168.1.10") for _ in range(5)]
        self.assertEqual(results, [True] * 4 + [False])
        relay._apply_config(make_cfg(client_rate_limit=2, listen_address="192.168.1.2"))
        self.assertTrue(all(relay.accept_client("192.168.1.2") for _ in range(20)))


class HotFallbackTests(unittest.TestCase):
    """Bascule à chaud ioctl -> pfctl sur une erreur de structure."""

    def make(self, err):
        dev = FakePfDev()
        dev.tables["APP_T"] = []
        ioctl = pio.PfIoctl(ioctl=dev, opener=lambda d: -1)
        ioctl.fallback_reason = None
        relay = appaliasd.Relay(make_cfg(), ioctl, asn_cache_path=NO_ASN)
        fake = FakePf()
        relay.pfctl_factory = lambda: PfTable(pfctl=["pfctl"], runner=fake)
        dev.fail_with = OSError(err, os.strerror(err))
        return relay, fake

    def test_einval_bascule(self):
        relay, fake = self.make(errno.EINVAL)
        with self.assertLogs("appalias", level="ERROR") as logs:
            ok = asyncio.run(relay._pf("APP_T", relay.pf.add, ["192.0.2.1"]))
        self.assertTrue(ok)
        self.assertEqual(relay.pf.name, "pfctl")
        self.assertIn("Errno %d" % errno.EINVAL, relay.pf.fallback_reason)
        self.assertEqual(fake.contents("APP_T"), {"192.0.2.1"})       # appel rejoué
        self.assertTrue(any("bascule définitive" in line for line in logs.output))
        self.assertEqual(relay.daemon_info()["pf_backend"], "pfctl")
        self.assertIsNotNone(relay.daemon_info()["pf_backend_reason"])

    def test_autre_erreur_pas_de_bascule(self):
        relay, fake = self.make(errno.EPERM)
        with self.assertLogs("appalias", level="ERROR"):
            ok = asyncio.run(relay._pf("APP_T", relay.pf.add, ["192.0.2.1"]))
        self.assertFalse(ok)
        self.assertEqual(relay.pf.name, "ioctl")
        self.assertEqual(fake.calls, [])


class PfBackendTests(unittest.TestCase):
    def test_changement_au_rechargement(self):
        relay = make_relay(pf_backend="pfctl")
        with self.assertLogs("appalias", level="INFO") as cm:
            relay._apply_config(make_cfg(pf_backend="ioctl"))
        self.assertTrue(any("redémarrage nécessaire" in line and "ioctl" in line
                            for line in cm.output))

    def test_daemon_info(self):
        relay = make_relay()
        info = relay.daemon_info()
        self.assertEqual(info["pf_backend"], "pfctl")
        self.assertEqual(info["pf_backend_mode"], "auto")
        self.assertIsNone(info["pf_backend_reason"])

    def test_raison_du_repli(self):
        def boom():
            raise OSError("pas de /dev/pf")

        with self.assertLogs("appalias", level="WARNING"):
            pf = make_backend("auto", ioctl_factory=boom)
        self.assertEqual(pf.fallback_reason, "pas de /dev/pf")
        self.assertIsNone(make_backend("pfctl").fallback_reason)
        relay = appaliasd.Relay(make_cfg(), pf, asn_cache_path=NO_ASN)
        self.assertEqual(relay.daemon_info()["pf_backend_reason"], "pas de /dev/pf")


if __name__ == "__main__":
    unittest.main()
