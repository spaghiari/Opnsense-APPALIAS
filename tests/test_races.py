"""Concurrence dans le relais : verrou par table, restore asynchrone, pause après échec.

Ces tests appellent directement les coroutines du relais (sans socket).
Les deux premiers échouent si les appels pfctl d'une table ne sont pas
sérialisés par le verrou de la table.
"""

import asyncio
import time
import unittest

from appalias_testutil import TYPE_A, FakePf, build_query, build_response

import appaliasd
from lib import config as cfgmod
from lib import dnswire
from lib.pftable import PfTable

IP = "192.0.2.1"


class SlowOps(FakePf):
    """Faux pfctl dont certaines opérations sont lentes."""

    def __init__(self, slow=(), delay=0.3, **kw):
        super().__init__(**kw)
        self.slow = set(slow)
        self.slow_delay = delay

    def __call__(self, argv, timeout):
        op = argv[argv.index("-T") + 1]
        if op in self.slow:
            time.sleep(self.slow_delay)
        return super().__call__(argv, timeout)


def make_relay(fake, clock=time.time, **general):
    gen = {"upstream_address": "127.0.0.1"}
    gen.update(general)
    cfg = cfgmod.from_dict({"general": gen,
                            "apps": [{"name": "T", "domains": ["example.com"]}]})
    return appaliasd.Relay(cfg, PfTable(pfctl=["pfctl"], runner=fake), clock=clock)


def response(ip=IP, qid=0x1234, name="www.example.com"):
    raw = build_response(name, [(name, TYPE_A, 60, ip)], qid=qid)
    return raw, dnswire.question_key(build_query(name, qid=qid))


class RaceTests(unittest.TestCase):
    def test_sweep_during_add(self):
        fake = SlowOps(slow={"delete"})
        relay = make_relay(fake)
        fake.tables["APP_T"] = {IP}
        relay.state.set("APP_T", IP, time.time() - 1)   # expirée
        raw, key = response()

        async def scenario():
            sweep = asyncio.ensure_future(relay.sweep())
            await asyncio.sleep(0.01)                     # delete en cours
            await relay.process_response(raw, expect=key)  # nouvel add de la même IP
            await sweep

        asyncio.run(scenario())
        self.assertTrue(relay.state.is_known("APP_T", IP, time.time()))
        self.assertIn(IP, fake.contents("APP_T"))
        ops = [op for op, _t, _a in fake.calls]
        self.assertEqual(ops, ["delete", "add"])

    def test_reload_during_add(self):
        fake = SlowOps(slow={"replace"})
        relay = make_relay(fake)
        raw, key = response()

        async def scenario():
            restore = asyncio.ensure_future(relay.restore_tables())
            await asyncio.sleep(0.01)                     # replace en cours (calculé sans l'IP)
            await relay.process_response(raw, expect=key)
            await restore

        asyncio.run(scenario())
        self.assertTrue(relay.state.is_known("APP_T", IP, time.time()))
        self.assertIn(IP, fake.contents("APP_T"))
        self.assertEqual([op for op, _t, _a in fake.calls], ["replace", "add"])

    def test_disable_during_pending_add(self):
        fake = SlowOps(slow={"replace"})
        relay = make_relay(fake)
        raw, key = response()
        disabled = cfgmod.from_dict({
            "general": {"upstream_address": "127.0.0.1"},
            "apps": [{"name": "T", "domains": ["example.com"], "enabled": False}]})

        async def scenario():
            holder = asyncio.ensure_future(relay._lock("APP_T").acquire())
            await holder                                  # verrou tenu : l'add va attendre
            add = asyncio.ensure_future(relay.process_response(raw, expect=key))
            await asyncio.sleep(0.01)
            self.assertIn(("APP_T", IP), relay._inflight)
            relay._apply_config(disabled)                 # rechargement : application désactivée
            relay._lock("APP_T").release()
            await asyncio.wait_for(add, 2)                # la future inflight est bien résolue
            await relay.restore_tables()

        asyncio.run(scenario())
        self.assertNotIn(IP, fake.contents("APP_T"))
        self.assertFalse(relay.state.is_known("APP_T", IP, time.time()))
        self.assertEqual(fake.ops("add"), [])
        self.assertEqual(relay._inflight, {})

    def test_exclusion_during_pending_add(self):
        fake = SlowOps()
        relay = make_relay(fake)
        raw, key = response()
        excluded = cfgmod.from_dict({
            "general": {"upstream_address": "127.0.0.1", "exclude_networks": ["192.0.2.0/24"]},
            "apps": [{"name": "T", "domains": ["example.com"]}]})

        async def scenario():
            await relay._lock("APP_T").acquire()
            add = asyncio.ensure_future(relay.process_response(raw, expect=key))
            await asyncio.sleep(0.01)
            relay._apply_config(excluded)                 # exclusion ajoutée pendant l'attente
            relay._lock("APP_T").release()
            await asyncio.wait_for(add, 2)

        asyncio.run(scenario())
        self.assertNotIn(IP, fake.contents("APP_T"))
        self.assertFalse(relay.state.is_known("APP_T", IP, time.time()))
        self.assertEqual(fake.ops("add"), [])
        self.assertEqual(relay._inflight, {})

    def test_reload_includes_inflight(self):
        fake = SlowOps(slow={"add"})
        relay = make_relay(fake)
        raw, key = response()
        seen = {}

        async def scenario():
            add = asyncio.ensure_future(relay.process_response(raw, expect=key))
            await asyncio.sleep(0.01)                     # add en cours, IP dans _inflight
            seen["inflight"] = ("APP_T", IP) in relay._inflight
            await relay.restore_tables()
            await add

        asyncio.run(scenario())
        self.assertTrue(seen["inflight"])
        self.assertIn(IP, fake.contents("APP_T"))
        self.assertEqual([op for op, _t, _a in fake.calls], ["add", "replace"])

    def test_restore_does_not_block_loop(self):
        fake = SlowOps(slow={"replace"})
        relay = make_relay(fake)
        ticks = []

        async def ticker():
            for _ in range(5):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        async def scenario():
            await asyncio.gather(relay.restore_tables(), ticker())

        asyncio.run(scenario())
        self.assertEqual(len(ticks), 5)
        self.assertLess(ticks[-1] - ticks[0], 0.25)       # la boucle a tourné pendant le replace


class CooldownTests(unittest.TestCase):
    def test_cooldown_after_failure(self):
        now = [1000.0]
        fake = FakePf(delay=0.2, fail=True)
        relay = make_relay(fake, clock=lambda: now[0])

        async def run(ip, qid):
            raw, key = response(ip=ip, qid=qid)
            t0 = time.monotonic()
            await relay.process_response(raw, expect=key)
            return time.monotonic() - t0

        with self.assertLogs("appalias", level="ERROR") as cm:
            first = asyncio.run(run("192.0.2.10", 1))
            self.assertEqual(len(fake.ops("add")), 1)
            # pendant 30 s : aucun appel, pas d'attente, pas de nouveau log
            now[0] += 10
            second = asyncio.run(run("192.0.2.11", 2))
            self.assertEqual(len(fake.ops("add")), 1)
            self.assertLess(second, 0.1)
            self.assertGreaterEqual(first, 0.2)
            # après la fenêtre : nouvel essai
            now[0] += 25
            asyncio.run(run("192.0.2.12", 3))
            self.assertEqual(len(fake.ops("add")), 2)
        errors = [r for r in cm.records if r.levelname == "ERROR"]
        self.assertEqual(len(errors), 2)  # un par fenêtre
        self.assertIn("APP_T", relay.state.last_error)

    def test_success_clears_cooldown(self):
        now = [1000.0]
        fake = FakePf(fail=True)
        relay = make_relay(fake, clock=lambda: now[0])
        raw, key = response()
        with self.assertLogs("appalias", level="ERROR"):
            asyncio.run(relay.process_response(raw, expect=key))
        fake.fail = False
        now[0] += 31
        asyncio.run(relay.process_response(raw, expect=key))
        self.assertFalse(relay.in_cooldown("APP_T"))
        self.assertNotIn("APP_T", relay.state.last_error)
        self.assertIn(IP, fake.contents("APP_T"))


class MismatchTests(unittest.TestCase):
    def test_id_or_qname_mismatch(self):
        fake = FakePf()
        relay = make_relay(fake)
        raw, key = response(qid=0x1111)
        asyncio.run(relay.process_response(raw, expect=(0x2222, key[1])))
        asyncio.run(relay.process_response(raw, expect=(key[0], "other.example.com")))
        asyncio.run(relay.process_response(raw, expect=None))
        self.assertEqual(fake.calls, [])
        asyncio.run(relay.process_response(raw, expect=key))
        self.assertEqual(len(fake.ops("add")), 1)


class OptionsTests(unittest.TestCase):
    def test_defaults_hit_measure(self):
        fake = FakePf()
        relay = make_relay(fake)
        raw, key = response()

        async def fake_udp(query, host, port):
            return raw

        relay._udp_exchange = fake_udp
        asyncio.run(relay.process_response(raw, expect=key))
        self.assertIn("APP_T", relay.state.last_hit)
        timing = {}
        self.assertEqual(asyncio.run(relay.forward(build_query("www.example.com"), "udp", timing)), raw)
        self.assertIn("upstream_ms", timing)
        self.assertEqual(len(relay.metrics.upstreams), 1)

    def test_hit_false(self):
        fake = FakePf()
        relay = make_relay(fake)
        relay.state.hit("APP_T", 123.0)
        raw, key = response()
        asyncio.run(relay.process_response(raw, expect=key, hit=False, source="proactive"))
        self.assertEqual(relay.state.last_hit["APP_T"], 123.0)
        self.assertIn(IP, fake.contents("APP_T"))

    def test_unknown_source_same_behavior(self):
        fake = FakePf()
        relay = make_relay(fake)
        raw, key = response()
        asyncio.run(relay.process_response(raw, expect=key, source="autre"))
        self.assertIn(IP, fake.contents("APP_T"))
        self.assertIn("APP_T", relay.state.last_hit)

    def test_measure_false(self):
        relay = make_relay(FakePf())
        raw, _key = response()

        async def fake_udp(query, host, port):
            return raw

        relay._udp_exchange = fake_udp
        timing = {}
        resp = asyncio.run(relay.forward(build_query("www.example.com"), "udp", timing,
                                         measure=False))
        self.assertEqual(resp, raw)
        self.assertEqual(relay.metrics.upstreams, {})
        self.assertEqual(relay.metrics.version, 0)
        self.assertIn("upstream_ms", timing)


if __name__ == "__main__":
    unittest.main()
