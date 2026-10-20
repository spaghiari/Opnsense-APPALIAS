import json
import os
import tempfile
import unittest
from unittest import mock

import appalias_testutil  # noqa: F401

from lib import state as statemod
from lib.state import State, compute_expiry


class ExpiryTests(unittest.TestCase):
    def test_compute_expiry(self):
        self.assertEqual(compute_expiry(1000, 60, 300, 3600), 1000 + 300 + 3600)
        self.assertEqual(compute_expiry(1000, 900, 300, 3600), 1000 + 900 + 3600)
        self.assertEqual(compute_expiry(1000, 0, 0, 0), 1000)

    def test_known_and_expired(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 100)
        st.set("APP_A", "192.0.2.2", 200)
        self.assertTrue(st.is_known("APP_A", "192.0.2.1", 50))
        self.assertFalse(st.is_known("APP_A", "192.0.2.1", 100))
        self.assertFalse(st.is_known("APP_B", "192.0.2.1", 0))
        self.assertEqual(st.valid("APP_A", 150), ["192.0.2.2"])
        self.assertEqual(st.expired(150), {"APP_A": ["192.0.2.1"]})
        self.assertEqual(st.pop_expired(150), {"APP_A": ["192.0.2.1"]})
        self.assertEqual(st.count("APP_A"), 1)

    def test_refresh_keeps_latest(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 500)
        st.set("APP_A", "192.0.2.1", 300)
        self.assertEqual(st.tables["APP_A"]["192.0.2.1"], 500)
        st.set("APP_A", "192.0.2.1", 900)
        self.assertEqual(st.tables["APP_A"]["192.0.2.1"], 900)

    def test_prune(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 500)
        st.set("APP_B", "192.0.2.1", 500)
        st.hit("APP_B", 1)
        st.prune_tables({"APP_A"})
        self.assertEqual(list(st.tables), ["APP_A"])
        self.assertEqual(st.last_hit, {})


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 1234.5)
        st.set("APP_A", "2001:db8::1", 99)
        st.hit("APP_A", 10)
        st.error("APP_A", 11, "boom")
        st.save(self.path)
        again = State.load(self.path)
        self.assertEqual(again.tables, st.tables)
        self.assertEqual(again.last_hit, {"APP_A": 10.0})
        self.assertEqual(again.last_error["APP_A"]["message"], "boom")
        leftovers = [f for f in os.listdir(os.path.dirname(self.path)) if f != "state.json"]
        self.assertEqual(leftovers, [])

    def test_atomic_on_failure(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 1)
        st.save(self.path)
        with open(self.path, "rb") as fh:
            before = fh.read()
        st.set("APP_A", "192.0.2.2", 2)
        with mock.patch("lib.state.os.replace", side_effect=OSError("disque plein")):
            with self.assertRaises(OSError):
                st.save(self.path)
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        leftovers = [f for f in os.listdir(os.path.dirname(self.path)) if f != "state.json"]
        self.assertEqual(leftovers, [])

    def test_load_missing_or_corrupt(self):
        self.assertEqual(State.load(self.path).tables, {})
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{pas du json")
        with self.assertLogs("appalias", level="WARNING"):
            self.assertEqual(State.load(self.path).tables, {})

    def test_from_dict_tolerant(self):
        st = State.from_dict({"tables": {"APP_A": {"192.0.2.1": "12", "x": "bad"}, "B": []},
                              "last_hit": {"APP_A": "z"}, "last_error": []})
        self.assertEqual(st.tables, {"APP_A": {"192.0.2.1": 12.0}})
        self.assertEqual(st.last_hit, {})
        self.assertEqual(State.from_dict("nope").tables, {})

    def test_file_is_json(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 5)
        st.save(self.path)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], 2)


class NamesWildcardsTests(unittest.TestCase):
    """Version 2 : noms vus par table et jokers DNS."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_v2_aller_retour(self):
        st = State()
        st.set("APP_A", "192.0.2.1", 50)
        self.assertTrue(st.see("APP_A", "chat.example.com", 100))
        self.assertFalse(st.see("APP_A", "chat.example.com", 150))
        st.see("APP_B", "cdn.example.org", 120)
        st.set_wildcard("example.net", 200)
        st.save(self.path)
        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["names"]["APP_A"], {"chat.example.com": 150.0})
        again = State.load(self.path)
        self.assertEqual(again.names, {"APP_A": {"chat.example.com": 150.0},
                                       "APP_B": {"cdn.example.org": 120.0}})
        self.assertEqual(again.wildcards, {"example.net": 200.0})
        self.assertEqual(again.names_count("APP_A"), 1)
        self.assertEqual(again.names_count("APP_C"), 0)

    def test_lecture_v1(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "tables": {"APP_A": {"192.0.2.1": 10}},
                       "last_hit": {}, "last_error": {}}, fh)
        st = State.load(self.path)
        self.assertEqual(st.tables, {"APP_A": {"192.0.2.1": 10.0}})
        self.assertEqual(st.names, {})
        self.assertEqual(st.wildcards, {})
        st = State.from_dict({"names": {"APP_A": {"a.example.com": "x", "b.example.com": 3},
                                        "APP_B": []},
                              "wildcards": {"example.com": "bad", "example.org": 4}})
        self.assertEqual(st.names, {"APP_A": {"b.example.com": 3.0}})
        self.assertEqual(st.wildcards, {"example.org": 4.0})

    def test_plafond_par_table(self):
        st = State()
        for i in range(statemod.MAX_NAMES + 10):
            st.see("APP_A", "h%d.example.com" % i, 1000 + i)
        self.assertEqual(st.names_count("APP_A"), statemod.MAX_NAMES)
        self.assertNotIn("h0.example.com", st.names["APP_A"])            # plus anciens sortis
        self.assertIn("h%d.example.com" % (statemod.MAX_NAMES + 9), st.names["APP_A"])
        st.see("APP_A", "h20.example.com", 99999)                         # rafraîchi : reste
        st.see("APP_A", "neuf.example.com", 100000)
        self.assertIn("h20.example.com", st.names["APP_A"])
        self.assertEqual(st.names_count("APP_A"), statemod.MAX_NAMES)
        big = {"names": {"APP_A": {"n%d.example.com" % i: i for i in range(2000)}}}
        self.assertEqual(State.from_dict(big).names_count("APP_A"), statemod.MAX_NAMES)

    def test_purge_30_jours(self):
        st = State()
        now = 100 * 86400
        st.see("APP_A", "vieux.example.com", now - 31 * 86400)
        st.see("APP_A", "recent.example.com", now - 29 * 86400)
        st.see("APP_B", "vieux.example.org", now - 40 * 86400)
        self.assertEqual(st.prune_names(now), 2)
        self.assertEqual(st.names, {"APP_A": {"recent.example.com": now - 29 * 86400.0}})

    def test_jokers_7_jours(self):
        st = State()
        now = 100 * 86400
        st.set_wildcard("vieux.example", now - 8 * 86400)
        st.set_wildcard("recent.example", now - 86400)
        self.assertEqual(st.active_wildcards(now), ["recent.example"])
        self.assertEqual(st.prune_wildcards(now), 1)
        self.assertEqual(list(st.wildcards), ["recent.example"])
        st.clear_wildcard("recent.example")
        self.assertEqual(st.wildcards, {})

    def test_prune_tables_retire_les_noms(self):
        st = State()
        st.see("APP_A", "a.example.com", 1)
        st.see("APP_B", "b.example.com", 1)
        st.prune_tables({"APP_A"})
        self.assertEqual(list(st.names), ["APP_A"])


class RelectureStateTests(unittest.TestCase):
    def test_horodatages_non_finis_rejetes(self):
        st = State.from_dict({"names": {"APP_A": {"a.example.com": float("nan"),
                                                  "b.example.com": "inf",
                                                  "c.example.com": 7}},
                              "wildcards": {"x.example": float("-inf"), "y.example": 3}})
        self.assertEqual(st.names, {"APP_A": {"c.example.com": 7.0}})
        self.assertEqual(st.wildcards, {"y.example": 3.0})

    def test_plafond_jokers(self):
        data = {"wildcards": {"d%d.example" % i: i for i in range(statemod.MAX_WILDCARDS + 50)}}
        st = State.from_dict(data)
        self.assertEqual(len(st.wildcards), statemod.MAX_WILDCARDS)
        self.assertNotIn("d0.example", st.wildcards)
        st.set_wildcard("neuf.example", 10 ** 6)
        self.assertEqual(len(st.wildcards), statemod.MAX_WILDCARDS)
        self.assertIn("neuf.example", st.wildcards)


if __name__ == "__main__":
    unittest.main()
