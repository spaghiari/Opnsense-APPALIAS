import json
import os
import tempfile
import unittest
from unittest import mock

import appalias_testutil  # noqa: F401

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
        self.assertEqual(data["version"], 1)


if __name__ == "__main__":
    unittest.main()
