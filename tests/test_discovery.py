"""Moteur de découverte : chemin de réponse, agrégation, persistance, commandes."""

import asyncio
import builtins
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import appalias_testutil  # noqa: F401
from appalias_testutil import TYPE_A, TYPE_CNAME, FakePf, build_response

import appaliasd
from lib import config as cfgmod
from lib import discovery as discmod
from lib import dnswire
from lib.matcher import Matcher
from lib.pftable import PfTable

UUID = "0123abcd-0000-4000-8000-0000000000aa"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def answers(qname, chain_to=None, ip="192.0.2.1"):
    recs = []
    owner = qname
    if chain_to:
        for target in chain_to:
            recs.append((owner, TYPE_CNAME, 300, target))
            owner = target
    recs.append((owner, TYPE_A, 300, ip))
    resp = dnswire.parse_response(build_response(qname, recs))
    return resp, dnswire.cname_chain(resp)


def make_engine(rules, clock, **kw):
    matcher = Matcher(rules, platforms=frozenset(["cloudfront.net"]))
    return discmod.Engine(matcher.match_name, clock=clock, path=kw.pop("path", "/nonexistent/d.json"), **kw)


def feed(engine, clock, qname, chain_to=None, tables=None):
    resp, chain = answers(qname, chain_to)
    if tables is None:
        tables = set()
        for name in chain:
            tables |= engine.match_name(name)
    engine.observer.observe(resp.qname, chain, resp.answers, clock(), tables)


def drain(engine, now=None):
    return asyncio.run(engine.drain(now))


class ObserverTests(unittest.TestCase):
    def test_bounded_and_dropped(self):
        obs = discmod.Observer(maxlen=3)
        for i in range(5):
            obs.observe("a.example", ["a.example"], [], float(i), set())
        self.assertEqual(len(obs.queue), 3)
        self.assertEqual(obs.dropped, 2)

    def test_no_disk_no_computation(self):
        obs = discmod.Observer()
        chain = ["a.example"]
        with mock.patch.object(builtins, "open", side_effect=AssertionError("disk")):
            obs.observe("a.example", chain, [], 1.0, set())
        self.assertIs(obs.queue[0][2], chain)   # référence gardée telle quelle


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.engine = make_engine({"APP_X": ["full:www.example.com"]}, self.clock, window=5)

    def test_chain_and_target_index(self):
        feed(self.engine, self.clock, "www.example.com", ["edge.example-cdn.net"])
        self.clock.t += 1
        feed(self.engine, self.clock, "img.other.com", ["edge.example-cdn.net"])
        drain(self.engine)
        # noms non couverts encore en attente (fenêtre W)
        self.assertNotIn("edge.example-cdn.net", self.engine.names)
        self.clock.t += 6
        drain(self.engine)
        edge = self.engine.names["edge.example-cdn.net"]
        self.assertEqual(edge["links"], [["APP_X", "www.example.com"]])
        self.assertEqual(self.engine.targets["edge.example-cdn.net"]["names"], [["APP_X", "www.example.com"]])
        self.assertEqual(self.engine.names["img.other.com"]["targets"], ["edge.example-cdn.net"])
        self.assertIn("www.example.com", self.engine.covered)

    def test_cooccurrence_window(self):
        feed(self.engine, self.clock, "unrelated.org")
        self.clock.t += 3
        feed(self.engine, self.clock, "www.example.com")   # passage après le nom, dans ±W
        self.clock.t += 20
        feed(self.engine, self.clock, "far.org")
        self.clock.t += 6
        drain(self.engine)
        self.assertEqual(self.engine.names["unrelated.org"]["cooc"], {"APP_X": 1})
        self.assertEqual(self.engine.names["far.org"]["cooc"], {})
        self.assertEqual(self.engine.apps["APP_X"]["active"], 1)

    def test_ignored_and_nxdomain(self):
        self.engine.ignored = discmod.Ignored([("domain", "tracker.net")])
        feed(self.engine, self.clock, "a.tracker.net")
        self.engine.observer.observe("nx.example", ["nx.example"], [], self.clock(), set())
        self.clock.t += 10
        drain(self.engine)
        self.assertEqual(self.engine.names, {})

    def test_eviction(self):
        self.engine.max_names = 10
        for i in range(12):
            feed(self.engine, self.clock, "n%d.example.org" % i)
            self.clock.t += 0.1
        feed(self.engine, self.clock, "n0.example.org")
        self.clock.t += 10
        drain(self.engine)
        self.assertLessEqual(len(self.engine.names), 9)
        self.assertIn("n0.example.org", self.engine.names)   # deux vues : gardé

    def test_batch_limit_and_yield(self):
        for i in range(discmod.BATCH_MAX + 10):
            feed(self.engine, self.clock, "x%d.example.org" % i)
        done = drain(self.engine)
        self.assertEqual(done, discmod.BATCH_MAX)
        self.assertEqual(len(self.engine.observer.queue), 10)

    def test_learn_session(self):
        feed(self.engine, self.clock, "old.example.org")
        self.clock.t += 10
        drain(self.engine)
        self.engine.start_learn(self.clock() + 120, UUID)
        self.clock.t += 1
        feed(self.engine, self.clock, "old.example.org")
        feed(self.engine, self.clock, "fresh.example.org")
        self.clock.t += 10
        drain(self.engine)
        names = self.engine.learn["names"]
        self.assertFalse(names["old.example.org"]["new"])
        self.assertTrue(names["fresh.example.org"]["new"])
        self.assertTrue(self.engine.learn["active"])
        self.clock.t += 200
        drain(self.engine)
        self.assertFalse(self.engine.learn["active"])
        self.assertEqual(self.engine.learn["stopped"], "time")

    def test_learn_bounded(self):
        self.engine.start_learn(self.clock() + 10_000)
        self.assertLessEqual(self.engine.learn["until"], self.clock() + discmod.LEARN_MAX_SECONDS)


class LoadTests(unittest.TestCase):
    """Charge : aucune tranche de travail ne doit bloquer la boucle longtemps."""

    @staticmethod
    def max_gap(coro_factory):
        async def run():
            gaps = []
            done = asyncio.Event()

            async def ticker():
                last = time.perf_counter()
                while not done.is_set():
                    await asyncio.sleep(0)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            tick = asyncio.ensure_future(ticker())
            await asyncio.sleep(0)
            result = await coro_factory()
            done.set()
            await tick
            return result, max(gaps)
        return asyncio.run(run())

    def test_snapshot_50000_names(self):
        clock = Clock()
        engine = make_engine({}, clock, max_names=50000)
        for i in range(50000):
            engine.names["n%d.example.org" % i] = {
                "views": 3, "first": 1.0, "last": 2.0, "targets": ["edge%d.cdn.example" % (i % 50)],
                "links": [["APP_X", "www.example.com"]], "cooc": {"APP_X": 2}}
        start = time.perf_counter()
        full = engine.to_dict()
        whole = time.perf_counter() - start
        text, gap = self.max_gap(engine.snapshot_json)
        snap = json.loads(text)
        self.assertEqual(len(snap["names"]), 50000)
        self.assertEqual(snap, json.loads(json.dumps(full)))
        self.assertLess(gap, 0.05, "tranche de %.1f ms (to_dict entier : %.1f ms)" % (gap * 1000, whole * 1000))

    def test_drain_slices_with_many_tables(self):
        clock = Clock()
        rules = {"APP_%d" % i: ["full:www%d.example.com" % i] for i in range(200)}
        engine = make_engine(rules, clock, window=5)
        for i in range(200):
            feed(engine, clock, "www%d.example.com" % i)
        for i in range(discmod.BATCH_MAX):
            feed(engine, clock, "u%d.example.org" % i)
        clock.t += 10
        _done, gap = self.max_gap(lambda: engine.drain(clock()))
        self.assertLess(gap, 0.05)
        self.assertEqual(len(engine.names["u0.example.org"]["cooc"]), discmod.MAX_COOC)

    def test_passages_sorted_and_bisect(self):
        clock = Clock()
        engine = make_engine({"APP_X": ["full:www.example.com"]}, clock, window=5)
        feed(engine, clock, "www.example.com")
        clock.t -= 100                     # horloge qui recule : insertion triée
        feed(engine, clock, "www.example.com")
        drain(engine)
        pas = list(engine.passages["APP_X"])
        self.assertEqual(pas, sorted(pas))
        self.assertEqual(engine._cooccurring(clock() + 3, frozenset()), ["APP_X"])
        self.assertEqual(engine._cooccurring(clock() + 50, frozenset()), [])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "discovery.json")
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_purge(self):
        engine = make_engine({"APP_X": ["full:www.example.com"]}, self.clock, path=self.path)
        feed(engine, self.clock, "www.example.com", ["edge.example-cdn.net"])
        feed(engine, self.clock, "keep.example.org")
        self.clock.t += 10
        drain(engine)
        engine.names["old.example.org"] = dict(engine.names["keep.example.org"], last=self.clock() - 30 * 86400)
        discmod.write_json(self.path, engine.to_dict())
        again = make_engine({}, self.clock, path=self.path, retention_days=7)
        self.assertTrue(again.load())
        self.assertIn("keep.example.org", again.names)
        self.assertNotIn("old.example.org", again.names)
        self.assertEqual(again.names["edge.example-cdn.net"]["links"], [["APP_X", "www.example.com"]])

    def test_corrupted_is_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        engine = make_engine({}, self.clock, path=self.path)
        self.assertFalse(engine.load())
        self.assertEqual(engine.names, {})
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "names": {"bad name!": {"views": 1}, "ok.example": {"views": "x"}}}, fh)
        self.assertTrue(engine.load())
        self.assertEqual(engine.names, {})


class CtlTests(unittest.TestCase):
    def test_parse_ctl(self):
        now = 1000.0
        self.assertEqual(discmod.parse_ctl('{"cmd": "dump"}', now), {"cmd": "dump"})
        out = discmod.parse_ctl(json.dumps({"cmd": "learn", "until": now + 120, "app_uuid": UUID}), now)
        self.assertEqual(out["app_uuid"], UUID)
        for bad in ('{"cmd": "learn", "until": 5000}', '{"cmd": "learn", "until": NaN}',
                    '{"cmd": "dump", "x": 1}', '["dump"]', '{"cmd": "rm"}', 'nope',
                    '{"cmd": "learn", "until": 1100, "app_uuid": "../x"}'):
            with self.assertRaises(ValueError, msg=bad):
                discmod.parse_ctl(bad, now)


def relay_config(discovery=True, **gen):
    general = {"enabled": True, "upstream_address": "127.0.0.1", "upstream_port": 53,
               "loglevel": "error", "discovery_enabled": discovery, "discovery_window": 5}
    general.update(gen)
    return cfgmod.from_dict({"general": general,
                             "apps": [{"uuid": UUID, "name": "X", "domains": ["full:www.example.com"]}]})


class RelayHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.fake = FakePf()
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def relay(self, discovery=True):
        return appaliasd.Relay(relay_config(discovery),
                               PfTable(pfctl=["pfctl"], runner=self.fake, tmpdir=self.tmp.name),
                               state_path=self.state, config_path="/nonexistent", clock=self.clock)

    def test_unmatched_answer_no_wait_no_disk(self):
        relay = self.relay()
        raw = build_response("other.example.org", [("other.example.org", TYPE_A, 60, "192.0.2.7")])
        coro = relay.process_response(raw)
        with mock.patch.object(builtins, "open", side_effect=AssertionError("disk")), \
                mock.patch.object(os, "replace", side_effect=AssertionError("disk")):
            with self.assertRaises(StopIteration):
                coro.send(None)          # terminé sans jamais rendre la main
        self.assertEqual(len(relay.discovery.observer.queue), 1)
        self.assertEqual(self.fake.calls, [])

    def test_same_pf_and_answer_when_enabled(self):
        raw = build_response("www.example.com", [("www.example.com", TYPE_A, 60, "198.51.100.9")])
        results = []
        for discovery in (False, True):
            self.fake = FakePf()
            relay = self.relay(discovery)
            asyncio.run(relay.process_response(raw))
            results.append((self.fake.calls, relay.state.tables))
        self.assertEqual(results[0], results[1])

    def test_observe_cost(self):
        relay = self.relay()
        obs = relay._observer
        start = time.perf_counter()
        for i in range(20000):
            obs.observe("a.example", ["a.example"], [], 1.0, set())
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertEqual(obs.dropped, 20000 - discmod.QUEUE_MAX)

    def test_disabled_erases_file(self):
        path = os.path.join(self.tmp.name, "discovery.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}")
        relay = self.relay(discovery=False)
        self.assertIsNone(relay._observer)
        self.assertFalse(os.path.exists(path))

    def test_ctl_purge_and_learn(self):
        relay = self.relay()
        relay.discovery.names["x.example.org"] = {"views": 1, "first": 1.0, "last": 1.0,
                                                  "targets": [], "links": [], "cooc": {}}

        async def run(cmd):
            discmod.write_json(relay.ctl_path, cmd)
            await relay.discovery_ctl()

        asyncio.run(run({"cmd": "learn", "until": self.clock() + 60}))
        self.assertTrue(relay.discovery.learn["active"])
        self.assertTrue(os.path.exists(relay.discovery_path))     # dump après la commande
        self.assertFalse(os.path.exists(relay.ctl_path))
        asyncio.run(run({"cmd": "purge"}))
        self.assertEqual(relay.discovery.names, {})
        self.assertFalse(os.path.exists(relay.discovery_path))


if __name__ == "__main__":
    unittest.main()
