"""DNS over TLS vers l'amont : faux serveur DoT local (certificats de test de
fixtures/tls/), relais réel, faux pfctl."""

import asyncio
import os
import socket
import ssl
import struct
import tempfile
import threading
import time
import unittest

from appalias_testutil import TYPE_A, FakePf, build_query, encode_name

import appaliasd
from lib import config as cfgmod
from lib import dnswire
from lib import dot
from test_proxy_integration import (ZONE, RelayHarness, answer_for, app, free_port, make_config,
                                    recv_exact, tcp_query, udp_query)

TLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tls")
CA = os.path.join(TLS, "ca.pem")
OTHER_CA = os.path.join(TLS, "other-ca.pem")
CERT = os.path.join(TLS, "dot.test.pem")
KEY = os.path.join(TLS, "dot.test.key")

RCODE_SERVFAIL = 2


class FakeDotServer:
    """Serveur DoT minimal (TLS sur TCP, préfixe de longueur) sur 127.0.0.1.

    silent : accepte la connexion TCP sans jamais répondre (ni poignée de main).
    close_after_answer : ferme la connexion après chaque réponse.
    """

    def __init__(self, silent=False, close_after_answer=False):
        self.silent = silent
        self.close_after_answer = close_after_answer
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(CERT, KEY)
        self.handshakes = 0
        self.failed_handshakes = 0
        self.queries = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.conns = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def _accept_loop(self):
        while not self.stop.is_set():
            try:
                raw, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            raw.settimeout(None)
            with self.lock:
                self.conns.append(raw)
            if self.silent:
                continue
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            tls = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            with self.lock:
                self.failed_handshakes += 1
            raw.close()
            return
        with self.lock:
            self.handshakes += 1
            self.conns.append(tls)
        try:
            while not self.stop.is_set():
                size = struct.unpack("!H", recv_exact(tls, 2))[0]
                query = recv_exact(tls, size)
                with self.lock:
                    self.queries += 1
                resp = answer_for(query)
                tls.sendall(struct.pack("!H", len(resp)) + resp)
                if self.close_after_answer:
                    break
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def close(self):
        self.stop.set()
        with self.lock:
            conns = list(self.conns)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self.thread.join(2)
        self.sock.close()


class PlainUdpListener:
    """Écoute UDP en clair sur un port : compte les requêtes (repli en clair interdit)."""

    def __init__(self, port):
        self.queries = 0
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.2)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            self.queries += 1
            self.sock.sendto(answer_for(data), addr)

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()


def build_query_edns(qname, size, qtype=TYPE_A, qid=0x1234):
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 1)
    opt = b"\x00" + struct.pack("!HHIH", 41, size, 0, 0)
    return header + encode_name(qname) + struct.pack("!HH", qtype, 1) + opt


def udp_raw(port, query):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    try:
        s.sendto(query, ("127.0.0.1", port))
        return s.recvfrom(65535)[0]
    finally:
        s.close()


def dot_general(port, name="dot.test", fallback=()):
    return {"upstream_protocol": "dot", "upstream_port": port, "upstream_tls_name": name,
            "upstream_fallback": list(fallback), "loglevel": "debug"}


def rcode(msg):
    return struct.unpack("!H", msg[2:4])[0] & 0x000F


class DotRelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.servers = []
        self.harness = None

    def tearDown(self):
        if self.harness:
            self.harness.close()
        for server in self.servers:
            server.close()
        self.tmp.cleanup()

    def server(self, **kw):
        srv = FakeDotServer(**kw)
        self.servers.append(srv)
        return srv

    def start(self, fake, general, cafile=CA, apps=None):
        cfg = make_config(free_port(), [general["upstream_port"]],
                          apps if apps is not None else [app("TEST", ["example.com"])])
        cfg["general"].update(general)
        self.harness = RelayHarness(cfg, self.tmp.name, fake, tls_cafile=cafile)
        return self.harness

    def pool(self, port, name="dot.test"):
        return self.harness.relay._pools[cfgmod.Upstream("127.0.0.1", port, name)]

    def test_resolution_ok(self):
        srv = self.server()
        fake = FakePf(delay=0.2)
        h = self.start(fake, dot_general(srv.port))
        seen = {}
        data = udp_query(h.port, "www.example.com",
                         on_receive=lambda: seen.setdefault("t", fake.contents("APP_TEST")))
        # traitement inchangé : IP dans la table avant la réponse
        self.assertIn("192.0.2.10", seen["t"])
        resp = dnswire.parse_response(data)
        self.assertEqual(resp.id, 0x4242)
        self.assertEqual(len(resp.answers), 4)
        data = tcp_query(h.port, "tcp.example.com")
        self.assertEqual(len(dnswire.parse_response(data).answers), 1)
        self.assertEqual(srv.queries, 2)

    def test_connection_reused(self):
        srv = self.server()
        h = self.start(FakePf(), dot_general(srv.port))
        for i in range(6):
            udp_query(h.port, "other.example.net", qid=0x1000 + i)
        tcp_query(h.port, "other.example.net")
        self.assertEqual(srv.queries, 7)
        self.assertEqual(srv.handshakes, 1)          # une seule poignée de main
        self.assertEqual(self.pool(srv.port).handshakes, 1)

    def test_reconnect_when_server_closes(self):
        srv = self.server(close_after_answer=True)
        h = self.start(FakePf(), dot_general(srv.port))
        for i in range(3):
            data = udp_query(h.port, "other.example.net", qid=0x2000 + i)
            self.assertEqual(rcode(data), 0)
        self.assertEqual(srv.queries, 3)
        self.assertEqual(srv.handshakes, 3)

    def assert_refused(self, h, srv, plain):
        with self.assertLogs("appalias", "WARNING") as logs:
            data = udp_query(h.port, "www.example.com")
        self.assertEqual(rcode(data), RCODE_SERVFAIL)
        self.assertEqual(srv.queries, 0)
        self.assertEqual(plain.queries, 0)            # aucun repli en clair
        text = "\n".join(logs.output)
        self.assertIn("certificat refusé", text)
        self.assertIn("127.0.0.1 port %d" % srv.port, text)
        return text

    def test_wrong_name_refused(self):
        srv = self.server()
        plain = PlainUdpListener(srv.port)
        try:
            fake = FakePf()
            h = self.start(fake, dot_general(srv.port, name="wrong.test"))
            text = self.assert_refused(h, srv, plain)
            self.assertIn("wrong.test", text)
            self.assertEqual(fake.ops("add"), [])
        finally:
            plain.close()

    def test_unknown_ca_refused(self):
        srv = self.server()
        plain = PlainUdpListener(srv.port)
        try:
            h = self.start(FakePf(), dot_general(srv.port), cafile=OTHER_CA)
            self.assert_refused(h, srv, plain)
        finally:
            plain.close()

    def test_silent_server_falls_back(self):
        dead = self.server(silent=True)
        good = self.server()
        fake = FakePf()
        h = self.start(fake, dot_general(dead.port, fallback=["127.0.0.1:%d" % good.port]))
        with self.assertLogs("appalias", "WARNING") as logs:
            data = udp_query(h.port, "www.example.com")
        self.assertEqual(rcode(data), 0)
        self.assertIn("192.0.2.10", fake.contents("APP_TEST"))
        self.assertEqual(good.queries, 1)
        self.assertIn("délai dépassé", "\n".join(logs.output))

    def test_fallback_tls_name_suffix(self):
        good = self.server()
        dead_port = free_port()   # rien n'écoute : connexion refusée
        h = self.start(FakePf(), dot_general(dead_port, name="nope.test",
                                             fallback=["127.0.0.1:%d#dot.test" % good.port]))
        data = udp_query(h.port, "www.example.com")
        self.assertEqual(rcode(data), 0)
        self.assertEqual(good.queries, 1)

    def test_failure_log_rate_limited(self):
        srv = self.server()
        h = self.start(FakePf(), dot_general(srv.port, name="wrong.test"))
        with self.assertLogs("appalias", "DEBUG") as logs:
            for i in range(3):
                udp_query(h.port, "www.example.com", qid=0x3000 + i)
        warnings = [l for l in logs.output if l.startswith("WARNING") and "certificat refusé" in l]
        self.assertEqual(len(warnings), 1)

    def test_udp_truncation(self):
        srv = self.server()
        fake = FakePf()
        h = self.start(fake, dot_general(srv.port))
        full = answer_for(build_query("large.example.com", qid=0x5353))
        self.assertGreater(len(full), 512)
        # sans EDNS : 512 octets au plus -> TC=1, question seule
        query = build_query("large.example.com", qid=0x5353)
        data = udp_raw(h.port, query)
        qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
        self.assertEqual((qid, qd, an, ns, ar), (0x5353, 1, 0, 0, 0))
        self.assertTrue(flags & dnswire.FLAG_TC)
        self.assertTrue(flags & dnswire.FLAG_QR)
        self.assertEqual(data[12:], query[12:])
        # traitement inchangé : les IP de la réponse complète sont ajoutées
        self.assertEqual(len(fake.contents("APP_TEST")), 40)
        # nouvel essai en TCP : réponse complète
        data = tcp_query(h.port, "large.example.com", qid=0x5353)
        self.assertEqual(data, full)
        # EDNS 4096 : la réponse tient, transmise entière en UDP
        data = udp_raw(h.port, build_query_edns("large.example.com", 4096, qid=0x5353))
        self.assertEqual(data, full)
        # EDNS 600 : trop petit
        data = udp_raw(h.port, build_query_edns("large.example.com", 600, qid=0x5354))
        self.assertTrue(struct.unpack("!H", data[2:4])[0] & dnswire.FLAG_TC)
        self.assertLessEqual(len(data), 600)

    def test_small_answer_not_truncated(self):
        srv = self.server()
        h = self.start(FakePf(), dot_general(srv.port))
        data = udp_query(h.port, "www.example.com", qid=0x4444)
        self.assertFalse(struct.unpack("!H", data[2:4])[0] & dnswire.FLAG_TC)
        self.assertEqual(data, answer_for(build_query("www.example.com", qid=0x4444)))

    def test_latency_logged_and_measured(self):
        srv = self.server()
        h = self.start(FakePf(), dot_general(srv.port))
        with self.assertLogs("appalias", "INFO") as logs:
            udp_query(h.port, "www.example.com")
        line = [l for l in logs.output if "APP_TEST : ajout de 2 IP" in l]
        self.assertEqual(len(line), 1, logs.output)
        self.assertRegex(line[0], r"\(amont \d+ ms, pf \d+ ms\)")
        summary = h.relay.metrics.to_dict()
        self.assertEqual(summary["upstreams"]["127.0.0.1@%d" % srv.port]["count"], 1)
        self.assertEqual(summary["pf"]["count"], 1)


class DotPoolTests(unittest.TestCase):
    def test_idle_timeout_reconnects(self):
        srv = FakeDotServer()
        now = [1000.0]
        try:
            pool = dot.DotPool(cfgmod.Upstream("127.0.0.1", srv.port, "dot.test"),
                               dot.make_context(CA), clock=lambda: now[0])

            async def run():
                q = build_query("other.example.net")
                await pool.exchange(q)
                now[0] += 10
                await pool.exchange(q)          # réutilisée
                self.assertEqual(pool.handshakes, 1)
                now[0] += dot.IDLE_TIMEOUT + 1
                pool.prune()                    # inactive depuis plus de 30 s : fermée
                self.assertEqual(pool.idle_count, 0)
                await pool.exchange(q)
                self.assertEqual(pool.handshakes, 2)
                pool.close()

            asyncio.run(run())
        finally:
            srv.close()

    def test_pool_bounded(self):
        srv = FakeDotServer()
        try:
            pool = dot.DotPool(cfgmod.Upstream("127.0.0.1", srv.port, "dot.test"),
                               dot.make_context(CA), size=2)

            async def run():
                qs = [build_query("other.example.net", qid=i) for i in range(10)]
                resps = await asyncio.gather(*(pool.exchange(q) for q in qs))
                self.assertEqual([r[:2] for r in resps], [q[:2] for q in qs])
                self.assertLessEqual(pool.handshakes, 2)
                pool.close()

            asyncio.run(run())
        finally:
            srv.close()

    def test_context_settings(self):
        ctx = dot.make_context(CA)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)
        # magasin absent : CA par défaut de Python, vérification maintenue
        ctx = dot.make_context(os.path.join(TLS, "absent.pem"))
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_log_limiter(self):
        now = [0.0]
        lim = dot.LogLimiter(interval=60, clock=lambda: now[0])
        self.assertEqual(lim.check("a"), (True, 0))
        self.assertEqual(lim.check("a"), (False, 0))
        self.assertEqual(lim.check("b"), (True, 0))
        self.assertEqual(lim.check("a"), (False, 0))
        now[0] = 61
        self.assertEqual(lim.check("a"), (True, 2))
        self.assertEqual(lim.check("a"), (False, 0))

    def test_failure_reason(self):
        self.assertEqual(dot.failure_reason(asyncio.TimeoutError()), "délai dépassé")
        self.assertIn("fermée", dot.failure_reason(asyncio.IncompleteReadError(b"", 2)))
        self.assertIn("connexion impossible", dot.failure_reason(ConnectionRefusedError(61, "refused")))


class TruncateTests(unittest.TestCase):
    def test_payload_size(self):
        self.assertEqual(dnswire.udp_payload_size(build_query("a.example")), 512)
        self.assertEqual(dnswire.udp_payload_size(build_query_edns("a.example", 1232)), 1232)
        self.assertEqual(dnswire.udp_payload_size(build_query_edns("a.example", 100)), 512)
        self.assertEqual(dnswire.udp_payload_size(b"\x00" * 5), 512)
        self.assertEqual(dnswire.udp_payload_size(build_query_edns("a.example", 4096)[:-3]), 512)

    def test_fit_udp(self):
        q = build_query("large.example.com", qid=0x77)
        full = answer_for(q)
        small = answer_for(build_query("www.example.com", qid=0x77))
        self.assertIs(dnswire.fit_udp(q, small), small)
        cut = dnswire.fit_udp(q, full)
        self.assertEqual(cut[:2], b"\x00\x77")
        self.assertEqual(struct.unpack("!HHHH", cut[4:12]), (1, 0, 0, 0))
        self.assertEqual(cut[12:], q[12:])
        resp = dnswire.parse_message(cut)
        self.assertTrue(resp.truncated)
        self.assertEqual(resp.qname, "large.example.com")
        self.assertIs(dnswire.fit_udp(build_query_edns("large.example.com", 4096), full), full)
        self.assertIsNone(dnswire.fit_udp(q, None))

    def test_truncate_keeps_rcode_and_flags(self):
        q = build_query("large.example.com")
        full = bytearray(answer_for(q))
        full[3] = (full[3] & 0xF0) | 3   # NXDOMAIN
        cut = dnswire.truncate(q, bytes(full))
        flags = struct.unpack("!H", cut[2:4])[0]
        self.assertEqual(flags & 0x000F, 3)
        self.assertTrue(flags & dnswire.FLAG_TC)


if __name__ == "__main__":
    unittest.main()
