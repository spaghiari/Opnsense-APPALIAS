"""Résolution proactive dans le démon : sondes, rejeu TCP, DoT, rechargement, cooldown.

Les tests appellent directement proactive_pass / _probe (sans socket) avec un
faux amont (Relay.forward remplacé) et le faux pfctl de appalias_testutil.
"""

import asyncio
import logging
import os
import random
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from appalias_testutil import TYPE_A, TYPE_AAAA, TYPE_CNAME, FakePf, build_response

import appaliasd
from lib import config as cfgmod
from lib import dnswire
from lib import proactive
from lib.pftable import PfTable

NAME = "www.example.com"
IP = "192.0.2.1"


def make_cfg(enabled=True, app_enabled=True, **general):
    gen = {"upstream_address": "127.0.0.1", "proactive_enabled": enabled}
    gen.update(general)
    return cfgmod.from_dict({"general": gen, "apps": [
        {"name": "X", "domains": ["full:" + NAME], "families": "inet", "enabled": app_enabled}]})


class Upstream:
    """Faux amont : remplace Relay.forward et garde les appels (proto, measure)."""

    def __init__(self, relay, handler=None):
        self.calls = []
        self.handler = handler or (lambda query, proto: self.answer(query))
        relay.forward = self.forward

    @staticmethod
    def answer(query, ips=(IP,), ttl=60, tc=False, rcode=0):
        qid, qname = dnswire.question_key(query)
        raw = build_response(qname, [(qname, TYPE_A, ttl, ip) for ip in ips], qid=qid, tc=tc)
        if rcode:
            raw = raw[:3] + bytes([raw[3] & 0xF0 | rcode]) + raw[4:]
        return raw

    async def forward(self, query, proto, timing=None, measure=True):
        self.calls.append((proto, measure))
        res = self.handler(query, proto)
        if asyncio.iscoroutine(res):
            res = await res
        return res


def make_relay(cfg=None, fake=None, state_path=None, **kw):
    fake = fake or FakePf()
    relay = appaliasd.Relay(cfg or make_cfg(), PfTable(pfctl=["pfctl"], runner=fake),
                            state_path=state_path or appaliasd.cfgmod.STATE_PATH, **kw)
    due_now(relay)
    return relay, fake


def due_now(relay):
    """Scheduler d'intervalle nul : toutes les cibles sont échues tout de suite."""
    relay.sched = proactive.Scheduler(time.time, 0, 0, random.Random(1))
    relay.sched.load(relay._proactive_targets)


async def run_pass(relay):
    await relay.proactive_pass()
    await asyncio.gather(*list(relay._tasks))


class ProactiveDaemonTests(unittest.TestCase):
    def test_pass_fills_table_without_client(self):
        relay, fake = make_relay()
        up = Upstream(relay)
        asyncio.run(run_pass(relay))
        self.assertEqual(fake.contents("APP_X"), {IP})
        self.assertTrue(relay.state.is_known("APP_X", IP, time.time()))
        self.assertEqual(up.calls, [("udp", False)])        # measure=False
        self.assertEqual(relay.metrics.upstreams, {})        # latences clients intactes
        self.assertEqual(relay.metrics.proactive,
                         {"sent": 1, "ok": 1, "empty": 0, "fail": 0, "added": 1})
        self.assertIn("proactive", relay.metrics.to_dict())

    def test_known_ip_is_refreshed_without_pf_add(self):
        relay, fake = make_relay()
        Upstream(relay)
        fake.tables["APP_X"] = {IP}
        relay.state.set("APP_X", IP, time.time() + 5)
        before = relay.state.tables["APP_X"][IP]
        asyncio.run(run_pass(relay))
        self.assertEqual(fake.ops("add"), [])
        self.assertGreater(relay.state.tables["APP_X"][IP], before)
        self.assertEqual(relay.metrics.proactive["added"], 0)

    def test_truncated_udp_is_replayed_in_tcp(self):
        relay, fake = make_relay()
        up = Upstream(relay, lambda q, proto: Upstream.answer(q, ips=(), tc=True)
                      if proto == "udp" else Upstream.answer(q))
        asyncio.run(run_pass(relay))
        self.assertEqual(up.calls, [("udp", False), ("tcp", False)])
        self.assertEqual(fake.contents("APP_X"), {IP})

    def test_dot_never_falls_back_to_clear(self):
        cfg = make_cfg(upstream_protocol="dot", upstream_tls_name="dns.example")
        relay, fake = make_relay(cfg)
        sent = []

        async def exchange(query):
            sent.append(query)
            return Upstream.answer(query, ips=(), tc=True)

        async def clear(*args, **kw):
            raise AssertionError("repli en clair interdit en DoT")

        (pool,) = relay._pools.values()
        pool.exchange = exchange
        relay._udp_exchange = clear
        relay._tcp_exchange = clear
        asyncio.run(run_pass(relay))
        self.assertEqual(len(sent), 1)
        self.assertEqual(fake.ops("add"), [])
        relay.close_pools()

    def test_app_disabled_during_resolution_is_not_filled(self):
        relay, fake = make_relay()
        gate = {}

        async def slow(query, proto):
            gate["started"].set()
            await gate["go"].wait()
            return Upstream.answer(query)

        async def scenario():
            gate["started"], gate["go"] = asyncio.Event(), asyncio.Event()
            Upstream(relay, slow)
            await relay.proactive_pass()
            await gate["started"].wait()
            relay._apply_config(make_cfg(app_enabled=False))   # rechargement pendant la sonde
            gate["go"].set()
            await asyncio.gather(*list(relay._tasks))

        asyncio.run(scenario())
        self.assertEqual(fake.ops("add"), [])
        self.assertEqual(fake.contents("APP_X"), set())
        self.assertEqual(relay.metrics.proactive["added"], 0)
        self.assertEqual(relay._proactive_active, 0)

    def test_config_change_discards_response_and_retries(self):
        relay, fake = make_relay()
        gate = {}

        async def slow(query, proto):
            gate["started"].set()
            await gate["go"].wait()
            return Upstream.answer(query)

        async def scenario():
            gate["started"], gate["go"] = asyncio.Event(), asyncio.Event()
            Upstream(relay, slow)
            await relay.proactive_pass()
            await gate["started"].wait()
            relay._apply_config(make_cfg())                    # même config, nouvelle génération
            gate["go"].set()
            await asyncio.gather(*list(relay._tasks))

        asyncio.run(scenario())
        self.assertEqual(fake.ops("add"), [])                  # réponse jetée
        self.assertEqual(len(relay.sched), 1)
        self.assertIsNotNone(relay.sched.next_due())           # cible reportée, pas perdue

    def test_cooldown_defers_without_query(self):
        relay, fake = make_relay()
        up = Upstream(relay)
        until = time.time() + 60
        relay._cooldown["APP_X"] = until
        asyncio.run(run_pass(relay))
        self.assertEqual(up.calls, [])
        self.assertEqual(relay.metrics.proactive["sent"], 0)   # rien n'est parti
        self.assertEqual(fake.ops("add"), [])
        self.assertGreaterEqual(relay.sched.next_due(), until)

    def test_cooldown_on_one_table_still_probes(self):
        cfg = cfgmod.from_dict({"general": {"upstream_address": "127.0.0.1",
                                            "proactive_enabled": True}, "apps": [
            {"name": n, "domains": ["full:" + NAME], "families": "inet", "enabled": True}
            for n in ("X", "Y")]})
        relay, fake = make_relay(cfg)
        up = Upstream(relay)
        relay._cooldown["APP_X"] = time.time() + 60            # APP_Y reste libre
        asyncio.run(run_pass(relay))
        self.assertEqual(len(up.calls), 1)
        self.assertEqual(relay.metrics.proactive["sent"], 1)
        self.assertEqual(relay.metrics.proactive["ok"], 1)

    def test_nxdomain_and_nodata_count_as_empty(self):
        for rcode, ips in ((3, ()), (0, ())):
            relay, fake = make_relay()
            Upstream(relay, lambda q, proto, r=rcode, i=ips: Upstream.answer(q, ips=i, rcode=r))
            asyncio.run(run_pass(relay))
            self.assertEqual(relay.metrics.proactive,
                             {"sent": 1, "ok": 0, "empty": 1, "fail": 0, "added": 0})
            self.assertEqual(fake.ops("add"), [])
            self.assertIsNotNone(relay.sched.next_due())       # replanifiée

    def test_tcp_replay_counts_one_more_sent(self):
        relay, _fake = make_relay()
        Upstream(relay, lambda q, proto: Upstream.answer(q, ips=(), tc=True)
                 if proto == "udp" else Upstream.answer(q))
        asyncio.run(run_pass(relay))
        self.assertEqual(relay.metrics.proactive["sent"], 2)
        self.assertEqual(relay.metrics.proactive["ok"], 1)

    def test_scheduler_recreated_during_probe_is_ignored(self):
        relay, fake = make_relay()
        gate = {}

        async def slow(query, proto):
            gate["started"].set()
            await gate["go"].wait()
            return Upstream.answer(query)

        async def scenario():
            gate["started"], gate["go"] = asyncio.Event(), asyncio.Event()
            Upstream(relay, slow)
            old = relay.sched
            await relay.proactive_pass()
            await gate["started"].wait()
            relay._apply_config(make_cfg(proactive_interval_min=120))   # nouveau Scheduler
            self.assertIsNot(relay.sched, old)
            new_due = relay.sched.next_due()
            gate["go"].set()
            await asyncio.gather(*list(relay._tasks))
            return new_due

        new_due = asyncio.run(scenario())
        self.assertEqual(fake.ops("add"), [])                  # ancienne réponse jetée
        self.assertEqual(relay.sched.next_due(), new_due)      # nouveau Scheduler intact
        self.assertEqual(relay._proactive_active, 0)

    def test_no_upstream_warning_is_rate_limited(self):
        relay, _fake = make_relay()

        async def down(*args, **kw):
            raise OSError("injoignable")

        relay._udp_exchange = down

        async def scenario():
            for _ in range(5):
                self.assertIsNone(await relay.forward(b"\x00\x00", "udp", measure=False))

        with self.assertLogs("appalias", level="WARNING") as cap:
            asyncio.run(scenario())
        msgs = [m for m in cap.output if "aucun amont n'a répondu" in m]
        self.assertEqual(len(msgs), 1)

    def test_entry_cap_is_respected(self):
        relay, fake = make_relay(make_cfg(max_entries_per_app=2))
        Upstream(relay, lambda q, proto: Upstream.answer(
            q, ips=("192.0.2.1", "192.0.2.2", "192.0.2.3")))
        asyncio.run(run_pass(relay))
        self.assertEqual(relay.state.count("APP_X"), 2)
        self.assertEqual(len(fake.contents("APP_X")), 2)

    def test_last_hit_stays_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg()
            relay, fake = make_relay(cfg, state_path=os.path.join(tmp, "state.json"))
            Upstream(relay)

            asyncio.run(run_pass(relay))
            self.assertEqual(fake.contents("APP_X"), {IP})
            self.assertNotIn("APP_X", relay.state.last_hit)

    def test_failure_outcomes_are_counted(self):
        relay, fake = make_relay()
        Upstream(relay, lambda q, proto: Upstream.answer(q, ips=(), rcode=2))   # SERVFAIL
        asyncio.run(run_pass(relay))
        self.assertEqual(relay.metrics.proactive["fail"], 1)
        self.assertEqual(fake.ops("add"), [])
        relay, fake = make_relay()
        Upstream(relay, lambda q, proto: None)                                  # délai
        asyncio.run(run_pass(relay))
        self.assertEqual(relay.metrics.proactive["fail"], 1)

    def test_probe_exception_is_failure(self):
        relay, _fake = make_relay()

        def boom(query, proto):
            raise RuntimeError("amont en panne")

        Upstream(relay, boom)
        asyncio.run(run_pass(relay))
        self.assertEqual(relay.metrics.proactive["fail"], 1)
        self.assertEqual(relay._proactive_active, 0)
        self.assertIsNotNone(relay.sched.next_due())                            # replanifiée

    def test_proactive_disabled_sends_nothing(self):
        relay, fake = make_relay(make_cfg(enabled=False))
        up = Upstream(relay)
        self.assertEqual(len(relay.sched), 0)
        relay.sched.load({(NAME, dnswire.TYPE_A): {"APP_X"}})   # même forcée : rien ne part
        asyncio.run(run_pass(relay))
        self.assertEqual(up.calls, [])
        self.assertEqual(fake.ops("add"), [])

    def test_reload_rebuilds_targets_and_scheduler(self):
        relay, _fake = make_relay()
        self.assertEqual(len(relay.sched), 1)
        old = relay.sched
        relay._apply_config(make_cfg(proactive_interval_min=120))
        self.assertIsNot(relay.sched, old)                       # intervalles changés
        self.assertEqual(relay.sched.interval_min, 120)
        same = relay.sched
        relay._apply_config(make_cfg(proactive_interval_min=120))
        self.assertIs(relay.sched, same)
        relay._apply_config(make_cfg(enabled=False))
        self.assertEqual(len(relay.sched), 0)

    def test_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            cfg = make_cfg(listen_address="127.0.0.1", listen_port=port)
            relay = appaliasd.Relay(cfg, PfTable(pfctl=["pfctl"], runner=FakePf()),
                                    state_path=os.path.join(tmp, "state.json"))
            relay.ready = threading.Event()

            async def scenario():
                main = asyncio.ensure_future(relay.run(install_signals=False))
                for _ in range(200):
                    if relay.ready.is_set():
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(relay.ready.is_set())
                task = relay._proactive_task
                self.assertIsNotNone(task)
                await asyncio.sleep(0.05)
                self.assertFalse(task.done())                    # la boucle vit
                relay.request_stop()
                await asyncio.wait_for(main, 5)
                return task

            task = asyncio.run(scenario())
            self.assertTrue(task.cancelled())
            self.assertIsNone(relay._proactive_task)


WILD_IP = "198.51.100.77"


def discovery_cfg(rules=("domain:signal.org",), deep=False, unbound=None, refs=(), **general):
    gen = {"upstream_address": "127.0.0.1", "proactive_enabled": True,
           "proactive_deep": deep, "proactive_prefixes": []}
    gen.update(general)
    data = {"general": gen, "apps": [{"name": "S", "domains": list(rules), "families": "inet",
                                      "catalog_refs": list(refs)}]}
    if unbound is not None:
        data["unbound"] = unbound
    return cfgmod.from_dict(data)


class Recorder:
    """Faux amont qui note les noms demandés ; nom aléatoire de sonde joker = WILD_IP
    (ou NXDOMAIN si wildcard=False), sinon IP."""

    def __init__(self, relay, wildcard=True):
        self.names = []
        self.wildcard = wildcard
        relay.forward = self.forward

    async def forward(self, query, proto, timing=None, measure=True):
        _qid, qname = dnswire.question_key(query)
        self.names.append(qname)
        first = qname.split(".", 1)[0]
        if len(first) == 12 and all(c in "0123456789abcdef" for c in first):
            if self.wildcard:
                return Upstream.answer(query, ips=(WILD_IP,))
            return Upstream.answer(query, ips=(), rcode=3)
        return Upstream.answer(query)


def relay_response(qname, ip=IP, cname=None):
    """Réponse de l'amont pour qname (éventuellement via un CNAME)."""
    answers = []
    target = qname
    if cname:
        answers.append((qname, TYPE_CNAME, 60, cname))
        target = cname
    answers.append((target, TYPE_A, 60, ip))
    return build_response(qname, answers, qid=7)


class DiscoveryDaemonTests(unittest.TestCase):
    """0.10 : noms vus, sonde joker, ingestion, amorçage Unbound."""

    def test_constantes(self):
        self.assertEqual(appaliasd.PROACTIVE_MAX_CONCURRENT, 4)
        self.assertEqual(appaliasd.PROACTIVE_RATE, 10.0)

    def test_nom_vu_par_le_relais_puis_sonde(self):
        relay, fake = make_relay(discovery_cfg())
        up = Recorder(relay)
        raw = relay_response("chat.signal.org", cname="edge.cdn.example.net")
        asyncio.run(relay.process_response(raw))
        self.assertEqual(fake.contents("APP_S"), {IP})
        self.assertEqual(set(relay.state.names["APP_S"]), {"chat.signal.org"})   # CNAME hors règles
        self.assertTrue(relay._targets_dirty)
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        asyncio.run(run_pass(relay))                       # moins de 60 s : pas de reconstruction
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        relay._targets_built -= appaliasd.TARGETS_REBUILD_INTERVAL
        up.names.clear()
        asyncio.run(run_pass(relay))
        key = ("chat.signal.org", TYPE_A)
        self.assertIn(key, relay._proactive_targets)
        self.assertFalse(relay._targets_dirty)
        # cible « lente » : servie après interval_min, derrière les cibles de base
        self.assertNotIn("chat.signal.org", up.names)
        st = relay.sched._state[key]
        self.assertTrue(st["slow"])
        self.assertGreaterEqual(st["due"], time.time() + relay.cfg.proactive_interval_min - 5)
        relay.sched.defer(key, time.time())                # échéance atteinte
        asyncio.run(run_pass(relay))
        self.assertIn("chat.signal.org", up.names)         # sondée ensuite
        self.assertEqual(relay.metrics.discovery["targets"], 2)

    def test_indices_du_catalogue(self):
        relay, _fake = make_relay(discovery_cfg(rules=("domain:signal.org",), refs=["signal"]))
        self.assertIn(("sfu.voip.signal.org", TYPE_A), relay._proactive_targets)
        self.assertNotIn(("textsecure-service.whispersystems.org", TYPE_A),
                         relay._proactive_targets)                 # hors des règles

    def test_sonde_joker_servie_en_premier(self):
        relay, _fake = make_relay(discovery_cfg(deep=True, refs=["signal"]))
        relay.labels = ["chat"]
        relay._update_proactive(relay.cfg)
        due = relay.sched.due(time.time() + 1, 1)
        self.assertEqual(due, [("signal.org", proactive.QTYPE_WILDCARD)])

    def test_reponse_hors_regles_non_enregistree(self):
        relay, _fake = make_relay(discovery_cfg())
        asyncio.run(relay.process_response(relay_response("www.example.net")))
        self.assertEqual(relay.state.names, {})
        self.assertFalse(relay._targets_dirty)

    def wild_relay(self):
        relay, fake = make_relay(discovery_cfg(rules=("domain:signal.org",), deep=True))
        relay.labels = ["chat", "cdn"]
        relay._update_proactive(relay.cfg)
        return relay, fake

    def test_sonde_joker_positive(self):
        relay, fake = self.wild_relay()
        self.assertIn(("signal.org", proactive.QTYPE_WILDCARD), relay._proactive_targets)
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)   # pas vérifié
        up = Recorder(relay, wildcard=True)
        with self.assertLogs("appalias", level="INFO") as cap:
            asyncio.run(run_pass(relay))
        self.assertTrue(any("joker" in m for m in cap.output))
        self.assertIn("signal.org", relay.state.wildcards)
        self.assertNotIn(WILD_IP, fake.contents("APP_S"))                          # jamais ajoutée
        self.assertEqual(fake.contents("APP_S"), {IP})
        self.assertEqual(set(relay.state.names["APP_S"]), {"signal.org"})          # nom aléatoire absent
        self.assertEqual(len([n for n in up.names if n.endswith(".signal.org")
                              and len(n.split(".")[0]) == 12]), 1)
        relay._targets_built -= appaliasd.TARGETS_REBUILD_URGENT
        asyncio.run(run_pass(relay))
        self.assertNotIn(("signal.org", proactive.QTYPE_WILDCARD), relay._proactive_targets)
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        self.assertIn(("signal.org", TYPE_A), relay._proactive_targets)

    def test_sonde_joker_negative_ajoute_le_dictionnaire(self):
        relay, fake = self.wild_relay()
        Recorder(relay, wildcard=False)
        asyncio.run(run_pass(relay))
        self.assertIn("signal.org", relay._wild_clear)
        self.assertEqual(relay.state.wildcards, {})
        relay._targets_built -= appaliasd.TARGETS_REBUILD_URGENT
        asyncio.run(run_pass(relay))
        self.assertIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        self.assertIn(("cdn.signal.org", TYPE_A), relay._proactive_targets)
        self.assertEqual(relay.metrics.discovery["targets"], 3)

    def test_joker_relu_depuis_letat(self):
        relay, _fake = make_relay(discovery_cfg(deep=True))
        relay.labels = ["chat"]
        relay.state.set_wildcard("signal.org", time.time())
        relay._wild_clear["signal.org"] = time.time()
        relay._update_proactive(relay.cfg)
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        self.assertNotIn(("signal.org", proactive.QTYPE_WILDCARD), relay._proactive_targets)

    def test_ingest(self):
        relay, fake = make_relay(discovery_cfg(max_entries_per_app=2))
        records = [("chat.signal.org.", "192.0.2.1", 60), ("CHAT.signal.org", "192.0.2.2", 60),
                   ("cdn.signal.org", "192.0.2.3", 60), ("other.example.net", "198.51.100.1", 60),
                   ("bad.signal.org", "pas-une-ip", 60), ("x",)]
        with self.assertLogs("appalias", level="WARNING"):                         # plafond
            names, added = asyncio.run(relay.ingest(records))
        self.assertEqual((names, added), (2, 2))
        self.assertEqual(relay.state.count("APP_S"), 2)
        self.assertEqual(len(fake.contents("APP_S")), 2)
        self.assertEqual(set(relay.state.names["APP_S"]), {"chat.signal.org", "cdn.signal.org"})
        self.assertEqual(relay.metrics.discovery["seeded"], 2)
        self.assertEqual(relay.metrics.to_dict()["discovery"]["seeded"], 2)
        self.assertNotIn("APP_S", relay.state.last_hit)                             # hit=False

    def test_ingest_respecte_exclusions_et_pause(self):
        relay, fake = make_relay(discovery_cfg(exclude_networks=["192.0.2.0/24"]))
        names, added = asyncio.run(relay.ingest([("chat.signal.org", "192.0.2.1", 60)]))
        self.assertEqual((names, added), (1, 0))
        self.assertEqual(fake.ops("add"), [])
        relay, fake = make_relay(discovery_cfg())
        relay._cooldown["APP_S"] = time.time() + 60
        self.assertEqual(asyncio.run(relay.ingest([("chat.signal.org", IP, 60)])), (1, 0))
        self.assertEqual(fake.ops("add"), [])

    def test_seed_from_unbound(self):
        relay, fake = make_relay(discovery_cfg(unbound={"enabled": True}))
        calls = []

        def collect():
            calls.append(1)
            return [("chat.signal.org", IP, 300), ("www.example.net", "198.51.100.1", 300)]

        relay.unboundcache_collect = collect
        with self.assertLogs("appalias", level="INFO") as cap:
            self.assertEqual(asyncio.run(relay.seed_from_unbound()), (1, 1))
        self.assertTrue(any("amorçage Unbound : 1 noms, 1 adresses" in m for m in cap.output))
        self.assertEqual(fake.contents("APP_S"), {IP})
        self.assertEqual(calls, [1])

    def test_seed_from_unbound_inactif(self):
        calls = []

        def collect():
            calls.append(1)
            return [("chat.signal.org", IP, 300)]

        for cfg in (discovery_cfg(unbound={"enabled": False}), discovery_cfg(),
                    discovery_cfg(unbound={"enabled": True}, unbound_cache_seed="0")):
            relay, fake = make_relay(cfg)
            relay.unboundcache_collect = collect
            self.assertIsNone(asyncio.run(relay.seed_from_unbound()))
            self.assertEqual(fake.ops("add"), [])
        relay, _fake = make_relay(discovery_cfg(unbound={"enabled": True}))
        relay.unboundcache_collect = None                  # module absent
        self.assertIsNone(asyncio.run(relay.seed_from_unbound()))
        self.assertEqual(calls, [])

    def test_seed_from_unbound_erreur_non_fatale(self):
        relay, fake = make_relay(discovery_cfg(unbound={"enabled": True}))

        def collect():
            raise RuntimeError("unbound-control en échec")

        relay.unboundcache_collect = collect
        with self.assertLogs("appalias", level="WARNING") as cap:
            self.assertIsNone(asyncio.run(relay.seed_from_unbound()))
        self.assertTrue(any("amorçage Unbound impossible" in m for m in cap.output))
        self.assertEqual(fake.ops("add"), [])

    def test_sweep_purge_noms_et_jokers(self):
        relay, _fake = make_relay(discovery_cfg())
        old = time.time() - 40 * 86400
        relay.state.see("APP_S", "vieux.signal.org", old)
        relay.state.set_wildcard("signal.org", old)
        relay._wild_clear["example.org"] = old
        asyncio.run(relay.sweep())
        self.assertEqual(relay.state.names, {})
        self.assertEqual(relay.state.wildcards, {})
        self.assertEqual(relay._wild_clear, {})
        self.assertTrue(relay._targets_dirty)


class RelectureDaemonTests(unittest.TestCase):
    """Corrections après relecture : plateformes, jokers, executor, amorçage unique,
    sonde joker AAAA, domaine inexistant, étalement au démarrage."""

    def test_ingest_ignore_les_plateformes_partagees(self):
        relay, fake = make_relay(discovery_cfg(rules=("domain:signal.org",
                                                      "full:cloudfront.net")))
        self.assertEqual(asyncio.run(relay.ingest([("cloudfront.net", IP, 60)])), (0, 0))
        self.assertEqual(fake.ops("add"), [])

    def test_ingest_et_noms_vus_sous_un_joker(self):
        relay, fake = make_relay(discovery_cfg())
        relay.state.set_wildcard("signal.org", time.time())
        names, added = asyncio.run(relay.ingest([("0123456789ab.signal.org", IP, 60),
                                                 ("signal.org", "192.0.2.9", 60)]))
        self.assertEqual((names, added), (1, 1))
        self.assertEqual(fake.contents("APP_S"), {"192.0.2.9"})
        asyncio.run(relay.process_response(relay_response("chat.signal.org")))
        self.assertEqual(set(relay.state.names["APP_S"]), {"signal.org"})   # chat : sous le joker

    def test_ingest_par_lots(self):
        relay, fake = make_relay(discovery_cfg())
        records = [("h%d.signal.org" % i, "192.0.%d.%d" % (i // 250, i % 250 + 1), 60)
                   for i in range(450)]
        self.assertEqual(asyncio.run(relay.ingest(records)), (450, 450))
        self.assertEqual(len(fake.contents("APP_S")), 450)
        self.assertEqual(len(fake.ops("add")), 3)          # 200 + 200 + 50

    def test_ingest_jete_si_rechargement(self):
        relay, fake = make_relay(discovery_cfg())
        real = appaliasd.plan_ingest

        def plan(*args):
            relay._config_gen += 1                          # rechargement pendant le tri
            return real(*args)

        with mock.patch.object(appaliasd, "plan_ingest", plan):
            with self.assertLogs("appalias", level="INFO") as cap:
                self.assertEqual(asyncio.run(relay.ingest([("chat.signal.org", IP, 60)])), (0, 0))
        self.assertTrue(any("abandonnée" in m for m in cap.output))
        self.assertEqual(fake.ops("add"), [])

    def test_un_seul_amorcage_a_la_fois(self):
        relay, fake = make_relay(discovery_cfg(unbound={"enabled": True}))
        started, release = threading.Event(), threading.Event()
        calls = []

        def collect():
            calls.append(1)
            started.set()
            release.wait(5)
            return [("chat.signal.org", IP, 300)]

        relay.unboundcache_collect = collect

        async def scenario():
            first = asyncio.ensure_future(relay.seed_from_unbound())
            while not started.is_set():
                await asyncio.sleep(0.01)
            with self.assertLogs("appalias", level="DEBUG") as cap:
                second = await relay.seed_from_unbound()
            release.set()
            result = await first
            # l'amorçage sauté est relancé une fois à la fin du premier
            for _ in range(500):
                if len(calls) >= 2 and not relay._seeding and not relay._tasks:
                    break
                await asyncio.sleep(0.01)
            return result, second, cap.output

        logger = logging.getLogger("appalias")
        level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            first, second, output = asyncio.run(scenario())
        finally:
            logger.setLevel(level)
        self.assertEqual(first, (1, 1))
        self.assertIsNone(second)
        self.assertEqual(calls, [1, 1])          # un seul à la fois, puis relance unique
        self.assertTrue(any("déjà en cours" in m for m in output))
        self.assertFalse(relay._seeding)
        self.assertFalse(relay._seed_again)

    def test_sonde_joker_aaaa(self):
        cfg = cfgmod.from_dict({"general": {"upstream_address": "127.0.0.1",
                                            "proactive_prefixes": []},
                                "apps": [{"name": "S", "domains": ["domain:signal.org"],
                                          "families": "inet6"}]})
        relay, fake = make_relay(cfg)
        relay.labels = ["chat"]
        relay._update_proactive(relay.cfg)
        asked = []

        async def forward(query, proto, timing=None, measure=True):
            qid, qname = dnswire.question_key(query)
            qtype = dnswire.parse_message(query).qtype
            asked.append((qname, qtype))
            if qname.split(".")[0] != "signal" and len(qname.split(".")[0]) == 12:
                return build_response(qname, [(qname, TYPE_AAAA, 60, "2001:db8::77")],
                                      qtype=TYPE_AAAA, qid=qid)
            return build_response(qname, [], qtype=TYPE_AAAA, qid=qid)

        relay.forward = forward
        due = relay.sched.due(time.time() + 1, 1)
        self.assertEqual(due, [("signal.org", proactive.QTYPE_WILDCARD)])
        outcome = asyncio.run(relay._probe_resolve(relay.sched, due[0], relay._config_gen))
        self.assertEqual(outcome[0], "ok")
        self.assertIn("signal.org", relay.state.wildcards)
        self.assertEqual([q for _n, q in asked], [TYPE_AAAA])   # inet6 : AAAA seulement
        self.assertEqual(fake.ops("add"), [])

    def test_domaine_inexistant(self):
        relay, _fake = make_relay(discovery_cfg(deep=True))
        relay.labels = ["chat"]
        relay._wild_clear["signal.org"] = time.time()
        relay._update_proactive(relay.cfg)
        self.assertIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        Upstream(relay, lambda q, proto: Upstream.answer(q, ips=(), rcode=3))
        key = ("signal.org", TYPE_A)
        with self.assertLogs("appalias", level="INFO") as cap:
            self.assertEqual(asyncio.run(relay._probe_resolve(relay.sched, key,
                                                              relay._config_gen))[0], "nxdomain")
        self.assertTrue(any("n'existe pas" in m for m in cap.output))
        self.assertIn("signal.org", relay._missing)
        relay._targets_built -= appaliasd.TARGETS_REBUILD_URGENT
        asyncio.run(run_pass(relay))
        self.assertNotIn(("chat.signal.org", TYPE_A), relay._proactive_targets)
        self.assertNotIn(("signal.org", proactive.QTYPE_WILDCARD), relay._proactive_targets)
        self.assertIn(key, relay._proactive_targets)       # revérifié au rythme du backoff
        Upstream(relay)
        asyncio.run(relay._probe_resolve(relay.sched, key, relay._config_gen))
        self.assertNotIn("signal.org", relay._missing)
        self.assertTrue(relay._targets_urgent)

    def test_demarrage_etale(self):
        relay, _fake = make_relay(discovery_cfg(rules=["full:h%d.signal.org" % i
                                                       for i in range(40)]))
        relay.sched = None
        relay._update_proactive(relay.cfg, spread=True)
        now = time.time()
        first = relay.sched.due(now + 1, 100)
        self.assertLess(len(first), 40)                    # pas de rafale au démarrage
        relay._apply_config(discovery_cfg(rules=["full:h%d.signal.org" % i
                                                 for i in range(41)]))
        self.assertIn(("h40.signal.org", TYPE_A), relay.sched.due(time.time() + 1, 100))


if __name__ == "__main__":
    unittest.main()
