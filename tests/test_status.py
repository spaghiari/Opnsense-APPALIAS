import json
import os
import tempfile
import unittest

from appalias_testutil import FakePf

import status
from lib.pftable import PfTable
from lib.state import State


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.state = os.path.join(self.tmp.name, "state.json")
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump({"general": {"enabled": False, "upstream_address": "192.0.2.53"},
                       "apps": [{"uuid": "u1", "name": "TEST", "domains": ["example.com"]}]}, fh)
        st = State()
        st.hit("APP_TEST", 42)
        st.error("APP_TEST", 43, "boom")
        st.save(self.state)
        self.fake = FakePf()
        self.fake.tables["APP_TEST"] = {"192.0.2.1", "192.0.2.2", "198.51.100.0/24", "2001:db8::1"}
        self.pf = PfTable(pfctl=["pfctl"], runner=self.fake)

    def tearDown(self):
        self.tmp.cleanup()

    def test_status(self):
        out = status.status(self.config, self.state, self.pf)
        self.assertEqual(out["status"], "disabled")
        app = out["apps"]["u1"]
        self.assertEqual((app["v4"], app["v6"]), (3, 1))
        self.assertEqual(app["last_hit"], 42.0)
        self.assertEqual(app["last_error"]["message"], "boom")

    def test_show_validates(self):
        self.assertIn("error", status.show("bad;name", self.config, self.pf))
        self.assertIn("error", status.show("APP_OTHER", self.config, self.pf))
        self.assertEqual(len(status.show("APP_TEST", self.config, self.pf)["entries"]), 4)
        self.assertEqual(self.fake.ops("show", "APP_OTHER"), [])

    def test_missing_config(self):
        out = status.status(os.path.join(self.tmp.name, "absent.json"), self.state, self.pf)
        self.assertEqual(out["status"], "stopped")
        self.assertTrue(out["error"])


if __name__ == "__main__":
    unittest.main()
