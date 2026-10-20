"""Test d'intégration : faux amont DNS (UDP + TCP), relais réel, faux pfctl."""

import asyncio
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest

from appalias_testutil import (TYPE_A, TYPE_AAAA, TYPE_CNAME, FakePf, build_query,
                               build_response)

import appaliasd
from lib import config as cfgmod
from lib import dnswire
from lib.pftable import PfTable

DEFAULT_EXCLUDES = ("0.0.0.0/8,127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
                    "100.64.0.0/10,169.254.0.0/16,::1/128,fc00::/7,fe80::/10")

ZONE = {
    "www.example.com": [("www.example.com", TYPE_A, 60, "192.0.2.10"),
                        ("www.example.com", TYPE_A, 60, "10.1.2.3"),
                        ("www.example.com", TYPE_AAAA, 60, "2001:db8::10"),
                        ("www.example.com", TYPE_AAAA, 60, "fe80::1")],
    "tcp.example.com": [("tcp.example.com", TYPE_A, 60, "192.0.2.20")],
    # adresses publiques : les préfixes ASN de documentation sont refusés (lib/asn.py)
    "asn.example.com": [("asn.example.com", TYPE_A, 60, "9.9.9.10"),
                        ("asn.example.com", TYPE_AAAA, 60, "2620:fe::10")],
    "alias.example.org": [("alias.example.org", TYPE_CNAME, 60, "edge.example.com"),
                          ("edge.example.com", TYPE_A, 60, "192.0.2.30")],
    "via.example.net": [("via.example.net", TYPE_CNAME, 60, "node.cdn.example.com"),
                        ("node.cdn.example.com", TYPE_A, 60, "192.0.2.40")],
    "other.example.net": [("other.example.net", TYPE_A, 60, "198.51.100.1")],
    "many.example.com": [("many.example.com", TYPE_A, 60, "192.0.2.%d" % i) for i in range(100, 105)],
    # réponse de plus de 512 octets (troncature UDP)
    "large.example.com": [("large.example.com", TYPE_A, 60, "198.51.100.%d" % i) for i in range(1, 41)],
}
TRUNCATED = "big.example.com"
UNBINDABLE = "192.0.2.123"            # adresse absente de la machine : bind impossible
WRONG_QNAME = "wrongq.example.com"   # l'amont répond avec un autre qname
WRONG_ID = "wrongid.example.com"     # l'amont répond avec un autre ID (TCP)


def free_port():
    for _ in range(50):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.bind(("127.0.0.1", 0))
        port = tcp.getsockname()[1]
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            udp.close()
            tcp.close()
    raise RuntimeError("aucun port libre")


def answer_for(query):
    qid = struct.unpack("!H", query[:2])[0]
    qname, pos = dnswire.read_name(query, 12)
    qtype = struct.unpack("!H", query[pos:pos + 2])[0]
    if qname == TRUNCATED:
        return build_response(qname, [], qtype=qtype, qid=qid, tc=True)
    if qname == WRONG_QNAME:
        return build_response("www.example.com", ZONE["www.example.com"], qtype=qtype, qid=qid)
    if qname == WRONG_ID:
        return build_response(qname, [(qname, TYPE_A, 60, "192.0.2.99")], qtype=qtype,
                              qid=qid ^ 0x0101)
    return build_response(qname, ZONE.get(qname, []), qtype=qtype, qid=qid)


class FakeUpstream:
    """Amont DNS minimal UDP + TCP sur 127.0.0.1."""

    def __init__(self, silent=False):
        self.port = free_port()
        self.silent = silent
        self.udp_queries = 0
        self.tcp_queries = 0
        self.stop = threading.Event()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", self.port))
        self.udp.settimeout(0.2)
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.bind(("127.0.0.1", self.port))
        self.tcp.listen(8)
        self.tcp.settimeout(0.2)
        self.threads = [threading.Thread(target=self._udp_loop, daemon=True),
                        threading.Thread(target=self._tcp_loop, daemon=True)]
        for t in self.threads:
            t.start()

    def _udp_loop(self):
        while not self.stop.is_set():
            try:
                data, addr = self.udp.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            self.udp_queries += 1
            if not self.silent:
                self.udp.sendto(answer_for(data), addr)

    def _tcp_loop(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.tcp.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                conn.settimeout(2)
                try:
                    size = struct.unpack("!H", recv_exact(conn, 2))[0]
                    query = recv_exact(conn, size)
                except (OSError, ConnectionError):
                    continue
                self.tcp_queries += 1
                if self.silent:
                    continue
                resp = answer_for(query)
                conn.sendall(struct.pack("!H", len(resp)) + resp)

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join(2)
        self.udp.close()
        self.tcp.close()


def recv_exact(sock, size):
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("fermé")
        buf += chunk
    return buf


class RelayHarness:
    def __init__(self, cfg_dict, tmpdir, fake, clock=None, state=None, tls_cafile=None,
                 asn_cache=None):
        self.config_path = os.path.join(tmpdir, "config.json")
        self.state_path = os.path.join(tmpdir, "state.json")
        # Cache ASN toujours dans tmpdir : jamais celui du système.
        self.asn_path = os.path.join(tmpdir, "asn.json")
        # Cache de réponses désactivé sauf demande explicite : les tests qui
        # comptent les requêtes amont (DoT, transfert Unbound) restent exacts.
        cfg_dict = dict(cfg_dict, general=dict(cfg_dict.get("general", {})))
        cfg_dict["general"].setdefault("cache_enabled", False)
        if asn_cache is not None:
            with open(self.asn_path, "w", encoding="utf-8") as fh:
                json.dump(asn_cache, fh)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg_dict, fh)
        if state is not None:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
        cfg = cfgmod.load(self.config_path)
        extra = {"tls_cafile": tls_cafile} if tls_cafile is not None else {}
        self.relay = appaliasd.Relay(cfg, PfTable(pfctl=["pfctl"], runner=fake, tmpdir=tmpdir),
                                     state_path=self.state_path, config_path=self.config_path,
                                     clock=clock or time.time, asn_cache_path=self.asn_path,
                                     **extra)
        self.relay.udp_timeout = 0.5
        self.relay.dot_timeout = 1.0
        self.relay.ready = threading.Event()
        self.thread = threading.Thread(
            target=lambda: asyncio.run(self.relay.run(install_signals=False)), daemon=True)
        self.thread.start()
        if not self.relay.ready.wait(5):
            raise RuntimeError("le relais n'a pas démarré")
        self.port = cfg.listen_port

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.relay.loop).result(10)

    def close(self):
        self.relay.loop.call_soon_threadsafe(self.relay.request_stop)
        self.thread.join(5)


def make_config(listen_port, upstreams, apps, **general):
    gen = {
        "enabled": True,
        "listen_address": "127.0.0.1",
        "listen_port": listen_port,
        "upstream_address": "127.0.0.1",
        "upstream_port": upstreams[0],
        "upstream_fallback": ["127.0.0.1:%d" % p for p in upstreams[1:]],
        "follow_cname": True,
        "ttl_min": 300,
        "ttl_margin": 3600,
        "max_entries_per_app": 20000,
        "exclude_networks": DEFAULT_EXCLUDES,
        "alias_prefix": "APP_",
        "loglevel": "error",
    }
    gen.update(general)
    return {"general": gen, "apps": apps}


def app(name, domains, networks=(), families="both", enabled=True, asns=()):
    return {"uuid": "uuid-" + name, "enabled": enabled, "name": name, "domains": list(domains),
            "networks": list(networks), "families": families, "origin": "manual",
            "asns": list(asns)}


def udp_query(port, qname, qtype=TYPE_A, qid=0x4242, on_receive=None, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    try:
        s.sendto(build_query(qname, qtype, qid), (host, port))
        data, _ = s.recvfrom(65535)
        if on_receive:
            on_receive()
        return data
    finally:
        s.close()


def tcp_query(port, qname, qtype=TYPE_A, qid=0x4343, on_receive=None):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        q = build_query(qname, qtype, qid)
        s.sendall(struct.pack("!H", len(q)) + q)
        size = struct.unpack("!H", recv_exact(s, 2))[0]
        if on_receive:
            on_receive()
        return recv_exact(s, size)


class ProxyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upstream = FakeUpstream()
        self.harness = None

    def tearDown(self):
        if self.harness:
            self.harness.close()
        self.upstream.close()
        self.tmp.cleanup()

    def start(self, apps, fake, upstreams=None, asn_cache=None, **general):
        upstreams = upstreams or [self.upstream.port]
        cfg = make_config(free_port(), upstreams, apps, **general)
        self.harness = RelayHarness(cfg, self.tmp.name, fake, asn_cache=asn_cache)
        return self.harness

    def test_ip_in_table_before_response(self):
        fake = FakePf(delay=0.3)
        h = self.start([app("TEST", ["example.com"])], fake)
        seen = {}
        data = udp_query(h.port, "www.example.com",
                         on_receive=lambda: seen.setdefault("t", fake.contents("APP_TEST")))
        self.assertIn("192.0.2.10", seen["t"])
        self.assertIn("2001:db8::10", seen["t"])
        resp = dnswire.parse_response(data)
        self.assertEqual(resp.id, 0x4242)
        self.assertEqual(len(resp.answers), 4)  # réponse transmise telle quelle

    def test_excluded_not_added(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        udp_query(h.port, "www.example.com")
        self.assertEqual(fake.contents("APP_TEST"), {"192.0.2.10", "2001:db8::10"})
        adds = fake.ops("add", "APP_TEST")
        self.assertEqual(len(adds), 1)  # un seul pfctl -T add par réponse
        self.assertEqual(sorted(adds[0][2]), ["192.0.2.10", "2001:db8::10"])

    def test_family_filter(self):
        fake = FakePf()
        h = self.start([app("V4", ["example.com"], families="inet")], fake)
        udp_query(h.port, "www.example.com")
        self.assertEqual(fake.contents("APP_V4"), {"192.0.2.10"})

    def test_known_ip_no_second_call(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        udp_query(h.port, "www.example.com")
        udp_query(h.port, "www.example.com", qid=0x4243)
        tcp_query(h.port, "www.example.com")
        self.assertEqual(len(fake.ops("add")), 1)

    def test_tcp_relay(self):
        fake = FakePf(delay=0.3)
        h = self.start([app("TEST", ["full:tcp.example.com"])], fake)
        seen = {}
        data = tcp_query(h.port, "tcp.example.com",
                         on_receive=lambda: seen.setdefault("t", fake.contents("APP_TEST")))
        # instantané pris dès la réception de l'en-tête de longueur : l'IP y est déjà
        self.assertIn("192.0.2.20", seen["t"])
        self.assertEqual(data, build_response("tcp.example.com", ZONE["tcp.example.com"], qid=0x4343))
        self.assertGreaterEqual(self.upstream.tcp_queries, 1)

    def test_truncated_passed_through(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        data = udp_query(h.port, TRUNCATED, qid=0x5151)
        self.assertEqual(data, build_response(TRUNCATED, [], qid=0x5151, tc=True))
        self.assertEqual(fake.ops("add"), [])

    def test_large_udp_answer_passed_through(self):
        # DNS clair, UDP -> UDP : la réponse de l'amont est rendue telle quelle,
        # sans troncature par le relais (l'amont applique lui-même la taille EDNS).
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        data = udp_query(h.port, "large.example.com", qid=0x5252)
        expected = build_response("large.example.com", ZONE["large.example.com"], qid=0x5252)
        self.assertGreater(len(expected), 512)
        self.assertEqual(data, expected)
        self.assertEqual(len(fake.contents("APP_TEST")), 40)

    def test_unmatched_and_cname(self):
        fake = FakePf()
        h = self.start([app("TEST", ["full:edge.example.com", "cdn.example.com"])], fake)
        udp_query(h.port, "other.example.net")
        self.assertEqual(fake.ops("add"), [])
        udp_query(h.port, "alias.example.org")   # qname non listé, cible CNAME listée
        udp_query(h.port, "via.example.net")
        self.assertEqual(fake.contents("APP_TEST"), {"192.0.2.30", "192.0.2.40"})

    def test_cname_not_followed(self):
        fake = FakePf()
        h = self.start([app("TEST", ["full:edge.example.com"])], fake, follow_cname=False)
        udp_query(h.port, "alias.example.org")
        self.assertEqual(fake.ops("add"), [])

    def test_fallback_upstream(self):
        dead = FakeUpstream(silent=True)
        try:
            fake = FakePf()
            h = self.start([app("TEST", ["example.com"])], fake,
                           upstreams=[dead.port, self.upstream.port])
            data = udp_query(h.port, "www.example.com")
            self.assertIsNotNone(dnswire.parse_response(data))
            self.assertGreaterEqual(dead.udp_queries, 1)
            self.assertIn("192.0.2.10", fake.contents("APP_TEST"))
        finally:
            dead.close()

    def test_wrong_qname_not_added(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        data = udp_query(h.port, WRONG_QNAME, qid=0x6161)
        # réponse transmise telle quelle, mais rien n'est ajouté
        self.assertEqual(data, build_response("www.example.com", ZONE["www.example.com"], qid=0x6161))
        self.assertEqual(fake.ops("add"), [])

    def test_wrong_id_tcp_not_added(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        data = tcp_query(h.port, WRONG_ID, qid=0x7070)
        self.assertEqual(struct.unpack("!H", data[:2])[0], 0x7070 ^ 0x0101)
        self.assertEqual(fake.ops("add"), [])

    def test_tcp_connection_cap(self):
        fake = FakePf()
        h = self.start([app("TEST", ["full:tcp.example.com"])], fake)
        h.relay.max_tcp_clients = 2
        idle = [socket.create_connection(("127.0.0.1", h.port), timeout=5) for _ in range(2)]
        try:
            deadline = time.time() + 5
            while h.relay._tcp_clients < 2 and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(h.relay._tcp_clients, 2)
            with socket.create_connection(("127.0.0.1", h.port), timeout=5) as extra:
                q = build_query("tcp.example.com")
                try:
                    extra.sendall(struct.pack("!H", len(q)) + q)
                    got = extra.recv(2)
                except (ConnectionError, OSError):
                    got = b""
                self.assertEqual(got, b"")  # fermée immédiatement, sans réponse
        finally:
            for s in idle:
                s.close()
        deadline = time.time() + 5
        while h.relay._tcp_clients > 0 and time.time() < deadline:
            time.sleep(0.02)
        self.assertIsNotNone(dnswire.parse_response(tcp_query(h.port, "tcp.example.com")))

    def test_cap(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"], networks=["198.51.100.0/24"])], fake,
                       max_entries_per_app=3)
        udp_query(h.port, "many.example.com")
        # 3 entrées max : 1 CIDR manuel + 2 IP DNS
        self.assertEqual(len(fake.contents("APP_TEST")), 3)

    def test_pf_failure_still_answers(self):
        fake = FakePf(fail=True)
        h = self.start([app("TEST", ["example.com"])], fake)
        data = udp_query(h.port, "www.example.com")
        self.assertIsNotNone(dnswire.parse_response(data))
        self.assertIn("APP_TEST", h.relay.state.last_error)


def asn_cache(prefixes, asn_number="2906"):
    return {"version": 1, "asns": {asn_number: {"prefixes": list(prefixes), "fetched": 1700000000,
                                                "error": None}}}


def answer_ips(data):
    resp = dnswire.parse_response(data)
    return sorted(str(ip) for ip, _ttl in dnswire.addresses(resp, dnswire.cname_chain(resp)))


class RelayFeaturesTests(unittest.TestCase):
    """0.9 : cache, préfixes ASN, écoute multiple, mesures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upstream = FakeUpstream()
        self.harness = None

    def tearDown(self):
        if self.harness:
            self.harness.close()
        self.upstream.close()
        self.tmp.cleanup()

    def start(self, apps, fake, upstreams=None, asn_cache=None, **general):
        upstreams = upstreams or [self.upstream.port]
        general.setdefault("proactive_enabled", False)   # compteurs amont déterministes
        general.setdefault("cache_enabled", True)         # désactivé par défaut dans RelayHarness
        cfg = make_config(free_port(), upstreams, apps, **general)
        self.harness = RelayHarness(cfg, self.tmp.name, fake, asn_cache=asn_cache)
        return self.harness

    def test_cache_hit(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        first = udp_query(h.port, "www.example.com", qid=0x1111)
        second = udp_query(h.port, "www.example.com", qid=0x2222)
        self.assertEqual(self.upstream.udp_queries, 1)
        resp = dnswire.parse_response(second)
        self.assertEqual(resp.id, 0x2222)
        self.assertEqual(answer_ips(second), answer_ips(first))
        self.assertEqual(fake.contents("APP_TEST"), {"192.0.2.10", "2001:db8::10"})
        cache = h.relay.metrics.to_dict()["cache"]
        self.assertEqual((cache["hits"], cache["misses"]), (1, 1))
        h.relay.save_metrics(force=True)
        with open(h.relay.metrics_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["cache"]["entries"], 1)
        self.assertEqual(saved["daemon"]["pf_backend"], "pfctl")

    def test_cache_hit_still_fills_table(self):
        # entrée retirée de la table entre deux requêtes : le hit la remet
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        udp_query(h.port, "www.example.com")
        h.relay.state.tables.get("APP_TEST", {}).clear()
        with fake.lock:
            fake.tables["APP_TEST"].clear()
        udp_query(h.port, "www.example.com", qid=0x4244)
        self.assertEqual(self.upstream.udp_queries, 1)
        self.assertEqual(fake.contents("APP_TEST"), {"192.0.2.10", "2001:db8::10"})

    def test_cache_disabled(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake, cache_enabled=False)
        udp_query(h.port, "www.example.com")
        udp_query(h.port, "www.example.com", qid=0x4243)
        self.assertEqual(self.upstream.udp_queries, 2)
        self.assertEqual(len(h.relay.cache), 0)

    def test_servfail_not_cached(self):
        dead = FakeUpstream(silent=True)
        try:
            fake = FakePf()
            h = self.start([app("TEST", ["example.com"])], fake, upstreams=[dead.port])
            for qid in (0x0A0A, 0x0B0B):
                data = udp_query(h.port, "www.example.com", qid=qid)
                self.assertEqual(struct.unpack("!H", data[2:4])[0] & 0xF, 2)   # SERVFAIL
            self.assertEqual(dead.udp_queries, 2)
            self.assertEqual(len(h.relay.cache), 0)
        finally:
            dead.close()

    def test_asn_prefixes_in_table(self):
        fake = FakePf()
        h = self.start([app("NF", ["example.com"], asns=["AS2906"])], fake,
                       asn_cache=asn_cache(["9.9.9.0/24", "2620:fe::/32"]))
        self.assertEqual(fake.contents("APP_NF"), {"9.9.9.0/24", "2620:fe::/32"})
        udp_query(h.port, "asn.example.com")
        self.assertEqual(fake.ops("add"), [])          # IP couvertes par les préfixes ASN
        self.assertEqual(h.relay.state.count("APP_NF"), 0)
        self.assertEqual(h.relay.metrics.to_dict()["asn"], {"tables": 1, "prefixes": 2})

    def test_asn_prefix_not_covering(self):
        fake = FakePf()
        h = self.start([app("NF", ["example.com"], asns=["AS2906"])], fake,
                       asn_cache=asn_cache(["8.8.8.0/24"]))
        self.assertEqual(fake.contents("APP_NF"), {"8.8.8.0/24"})
        udp_query(h.port, "asn.example.com")
        self.assertEqual(fake.contents("APP_NF"), {"8.8.8.0/24", "9.9.9.10", "2620:fe::10"})

    def test_asn_prefixes_bornes_au_plafond(self):
        fake = FakePf()
        with self.assertLogs("appalias", level="WARNING") as logs:
            self.start([app("NF", ["example.com"], networks=["1.1.1.0/24"], asns=["AS2906"])],
                       fake, asn_cache=asn_cache(["8.8.8.0/24", "9.9.9.0/24", "11.0.0.0/8"]),
                       max_entries_per_app=3)
        self.assertEqual(fake.contents("APP_NF"), {"1.1.1.0/24", "8.8.8.0/24", "9.9.9.0/24"})
        self.assertTrue(any("tronqués à 2" in line for line in logs.output))

    def test_asn_reload_apres_mise_a_jour(self):
        fake = FakePf()
        h = self.start([app("NF", ["example.com"], asns=["AS2906"])], fake,
                       asn_cache=asn_cache(["8.8.8.0/24"]))
        self.assertEqual(fake.contents("APP_NF"), {"8.8.8.0/24"})
        with open(h.asn_path, "w", encoding="utf-8") as fh:
            json.dump(asn_cache(["9.9.9.0/24", "2620:fe::/32"]), fh)
        h.call(h.relay.reload())                 # équivalent du configctl reload d'asn_update
        self.assertEqual(fake.contents("APP_NF"), {"9.9.9.0/24", "2620:fe::/32"})
        udp_query(h.port, "asn.example.com")
        self.assertEqual(fake.ops("add"), [])

    def test_listen_two_addresses(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("127.0.0.2", 0))
        except OSError:
            self.skipTest("127.0.0.2 indisponible sur cette machine")
        finally:
            probe.close()
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake,
                       listen_address=["127.0.0.1", "127.0.0.2"])
        self.assertEqual(len(h.relay._udp_transports), 2)
        for host in ("127.0.0.1", "127.0.0.2"):
            data = udp_query(h.port, "www.example.com", host=host)
            self.assertIn("192.0.2.10", answer_ips(data))
            with socket.create_connection((host, h.port), timeout=5) as s:
                q = build_query("tcp.example.com", TYPE_A, 0x5353)
                s.sendall(struct.pack("!H", len(q)) + q)
                size = struct.unpack("!H", recv_exact(s, 2))[0]
                self.assertEqual(dnswire.parse_response(recv_exact(s, size)).id, 0x5353)

    def test_listen_partiel_puis_reessai(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake,
                       listen_address=[UNBINDABLE, "127.0.0.1"])
        self.assertEqual(h.relay._listen_missing, {UNBINDABLE})
        self.assertEqual(len(h.relay._udp_transports), 1)
        self.assertIn("192.0.2.10", answer_ips(udp_query(h.port, "www.example.com")))
        h.call(h.relay.retry_listeners())        # toujours impossible : rien ne change
        self.assertEqual(h.relay._listen_missing, {UNBINDABLE})
        self.assertEqual(len(h.relay._udp_transports), 1)

    def test_reload_vers_adresses_impossibles(self):
        fake = FakePf()
        h = self.start([app("TEST", ["example.com"])], fake)
        cfg = make_config(h.port, [self.upstream.port], [app("TEST", ["example.com"])],
                          listen_address=[UNBINDABLE], proactive_enabled=False)
        with open(h.config_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        with self.assertLogs("appalias", level="ERROR") as logs:
            h.call(h.relay.reload())
        self.assertTrue(any("anciennes adresses conservées" in line for line in logs.output))
        self.assertEqual(h.relay._listen_spec, (("127.0.0.1",), h.port))
        self.assertIn("192.0.2.10", answer_ips(udp_query(h.port, "www.example.com")))
        self.assertFalse(h.relay._stop.is_set())


class ProxyIntegrationCachedTests(ProxyIntegrationTests):
    """Toute la classe ProxyIntegrationTests rejouée avec le cache de réponses actif."""

    def start(self, apps, fake, upstreams=None, asn_cache=None, **general):
        general.setdefault("cache_enabled", True)
        return super().start(apps, fake, upstreams=upstreams, asn_cache=asn_cache, **general)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.upstream = FakeUpstream()

    def tearDown(self):
        self.upstream.close()
        self.tmp.cleanup()

    def test_restore_sweep_reload(self):
        now = [100000.0]
        fake = FakePf()
        state = {"version": 1, "tables": {
            "APP_TEST": {"192.0.2.50": now[0] + 500, "192.0.2.51": now[0] - 1,
                         "10.9.9.9": now[0] + 500},
            "APP_GONE": {"192.0.2.60": now[0] + 500}}}
        cfg = make_config(free_port(), [self.upstream.port], [
            app("TEST", ["example.com"], networks=["198.51.100.0/24"]),
            app("OFF", ["example.org"], enabled=False),
        ], proactive_enabled=False)   # l'horloge saute de 100000 s : les sondes proactives seraient échues
        h = RelayHarness(cfg, self.tmp.name, fake, clock=lambda: now[0], state=state)
        try:
            # au démarrage : replace (IP valides non exclues + CIDR), flush des désactivées
            self.assertEqual(fake.contents("APP_TEST"), {"198.51.100.0/24", "192.0.2.50"})
            self.assertEqual(len(fake.ops("flush", "APP_OFF")), 1)
            self.assertNotIn("APP_GONE", h.relay.state.tables)

            # app désactivée : pas d'ajout
            udp_query(h.port, "alias.example.org")
            self.assertEqual(fake.ops("add", "APP_OFF"), [])

            udp_query(h.port, "www.example.com")
            self.assertIn("192.0.2.10", fake.contents("APP_TEST"))

            # balayage : tout expire sauf le CIDR manuel
            now[0] += 100000
            h.call(h.relay.sweep())
            self.assertEqual(fake.contents("APP_TEST"), {"198.51.100.0/24"})
            for _op, _t, args in fake.ops("delete"):
                self.assertNotIn("198.51.100.0/24", args)

            # rechargement (équivalent SIGHUP) : nouvelle app, replace de toutes les tables
            new_cfg = make_config(h.port, [self.upstream.port], [
                app("TEST", ["example.com"]), app("NEW", ["example.net"])],
                proactive_enabled=False)
            with open(h.config_path, "w", encoding="utf-8") as fh:
                json.dump(new_cfg, fh)
            h.call(h.relay.reload())
            self.assertEqual(fake.contents("APP_TEST"), set())
            self.assertEqual(len(fake.ops("replace", "APP_NEW")), 1)
            udp_query(h.port, "other.example.net")
            self.assertEqual(fake.contents("APP_NEW"), {"198.51.100.1"})
        finally:
            h.close()
        # arrêt : état sauvegardé
        with open(h.state_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertIn("198.51.100.1", saved["tables"]["APP_NEW"])


if __name__ == "__main__":
    unittest.main()
