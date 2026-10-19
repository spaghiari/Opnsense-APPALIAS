"""Résolution proactive dans le démon : sondes, rejeu TCP, DoT, rechargement, cooldown.

Les tests appellent directement proactive_pass / _probe (sans socket) avec un
faux amont (Relay.forward remplacé) et le faux pfctl de appalias_testutil.
"""

import asyncio
import os
import random
import socket
import tempfile
import threading
import time
import unittest

from appalias_testutil import TYPE_A, FakePf, build_response

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

    def test_discovery_and_last_hit_stay_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(discovery_enabled=True)
            relay, fake = make_relay(cfg, state_path=os.path.join(tmp, "state.json"))
            Upstream(relay)

            async def scenario():
                await run_pass(relay)
                await relay.discovery.drain()

            asyncio.run(scenario())
            self.assertEqual(fake.contents("APP_X"), {IP})
            self.assertEqual(len(relay.discovery.names), 0)
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


if __name__ == "__main__":
    unittest.main()
