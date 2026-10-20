# Copyright (C) 2026 spaghiari
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Préfixes BGP des ASN : module lib/asn.py et script asn_update.py (réseau simulé)."""

import io
import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import appalias_testutil  # noqa: F401

import asn_update
from lib import asn
from lib import config as cfgmod

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "asn", "as2906.json")
URL = asn.API_URL % 2906

CONFIG = {"general": {"enabled": True, "upstream_address": "192.0.2.53"},
          "apps": [{"name": "NF", "domains": [], "asns": ["AS2906", "bad"]}]}


def fixture_body():
    with open(FIXTURE, "rb") as fh:
        return fh.read()


class FakeResponse:
    def __init__(self, url, body, length=None):
        self.url = url
        self.body = io.BytesIO(body)
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def geturl(self):
        return self.url

    def read(self, n=-1):
        return self.body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """open(req, timeout) -> réponse ; handler(asn) -> corps, ou lève une exception."""

    def __init__(self, handler=None, final_url=None):
        self.handler = handler or (lambda number: fixture_body())
        self.final_url = final_url
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url)
        number = int(req.full_url.rsplit("=AS", 1)[1])
        body = self.handler(number)
        return FakeResponse(self.final_url or req.full_url, body)


def app(name, table, asns, enabled=True):
    return SimpleNamespace(name=name, table=table, asns=asns, enabled=enabled)


def entry(prefixes, fetched=1000, error=None):
    return {"prefixes": prefixes, "fetched": fetched, "error": error}


class ParseTest(unittest.TestCase):
    def test_valid(self):
        for text, expected in (("AS2906", 2906), ("as2906", 2906), (" 2906 ", 2906), ("4294967295", 4294967295),
                               (2906, 2906)):
            self.assertEqual(asn.parse_asn(text), expected, text)

    def test_invalid(self):
        for text in ("0", "AS0", "4294967296", "", "AS", "2906x", "-5", "AS 29", None, True, 1.5, "٣"):
            self.assertIsNone(asn.parse_asn(text), repr(text))

    def test_app_asns(self):
        self.assertEqual(asn.app_asns(app("A", "T", ["AS2906", "bad", "2906", "as15169", "", "AS2906"])),
                         [2906, 15169])
        self.assertEqual(asn.app_asns({"asns": ["AS1", "x"]}), [1])
        self.assertEqual(asn.app_asns(app("A", "T", None)), [])

    def test_needed_asns(self):
        cfg = SimpleNamespace(apps=[app("A", "T", ["AS1"]), app("B", "U", ["2", "AS1"], enabled=False)])
        self.assertEqual(asn.needed_asns(cfg), {1, 2})


class PrefixesTest(unittest.TestCase):
    def test_aggregation(self):
        cache = {"asns": {"1": entry(["9.9.9.0/25", "9.9.9.128/25"])}}
        self.assertEqual(asn.prefixes_for([1], cache), ["9.9.9.0/24"])

    def test_union_order_canonical(self):
        cache = {"asns": {"1": entry(["2a00:1450::/32", "8.8.8.7/32", "11.0.0.0/8"]),
                          "2": entry(["8.8.8.0/24", "2a00:1450:1::/48"])}}
        self.assertEqual(asn.prefixes_for([1, 2], cache), ["8.8.8.0/24", "11.0.0.0/8", "2a00:1450::/32"])

    def test_single_host_canonical(self):
        cache = {"asns": {"1": entry(["9.9.9.9/32", "2620:fe::fe/128"])}}
        self.assertEqual(asn.prefixes_for([1], cache), ["9.9.9.9", "2620:fe::fe"])

    def test_invalid_and_missing_ignored(self):
        cache = {"asns": {"1": entry(["bad", "9.9.9.0/24", "999.1.1.1/8"])}}
        self.assertEqual(asn.prefixes_for([1, 7], cache), ["9.9.9.0/24"])
        self.assertEqual(asn.prefixes_for([], cache), [])

    def test_cap(self):
        prefixes = ["11.%d.%d.0/24" % (i // 256, i % 256) for i in range(0, 300, 2)]
        old = asn.MAX_PREFIXES_PER_APP
        asn.MAX_PREFIXES_PER_APP = 10
        try:
            with self.assertLogs("appalias", level="WARNING") as logs:
                out = asn.prefixes_for([1], {"asns": {"1": entry(prefixes)}})
        finally:
            asn.MAX_PREFIXES_PER_APP = old
        self.assertEqual(len(out), 10)
        self.assertEqual(len(logs.records), 1)

    def test_by_table(self):
        cfg = SimpleNamespace(apps=[app("A", "APP_A", ["AS1"]),
                                    app("B", "APP_B", ["AS1"], enabled=False),
                                    app("C", "APP_C", [])])
        cache = {"asns": {"1": entry(["9.9.9.0/24"])}}
        self.assertEqual(asn.prefixes_by_table(cfg, cache), {"APP_A": ["9.9.9.0/24"]})

    def test_prefixes_refuses(self):
        refused = ["0.0.0.0/0", "8.0.0.0/7", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
                   "224.0.0.0/4", "240.0.0.0/4", "192.168.1.0/24", "192.0.2.0/24",
                   "100.64.0.0/10",
                   "::/0", "2000::/16", "fe80::/10", "ff00::/8", "::1/128", "fc00::/7"]
        cache = {"asns": {"1": entry(refused + ["8.8.8.0/24", "2a00:1450::/32"])}}
        with self.assertLogs("appalias", level="DEBUG") as logs:
            out = asn.prefixes_for([1], cache)
        self.assertEqual(out, ["8.8.8.0/24", "2a00:1450::/32"])
        self.assertIn("%d préfixes écartés" % len(refused), logs.output[0])

    def test_largeurs_limites(self):
        cache = {"asns": {"1": entry(["11.0.0.0/8", "2a00::/19"])}}
        self.assertEqual(asn.prefixes_for([1], cache), ["11.0.0.0/8", "2a00::/19"])


class StaleTest(unittest.TestCase):
    def test_cases(self):
        now = 1_000_000
        self.assertTrue(asn.is_stale(entry([], fetched=None), now))
        self.assertTrue(asn.is_stale(entry([], fetched=now - asn.REFRESH), now))
        self.assertFalse(asn.is_stale(entry([], fetched=now - 100), now))
        self.assertTrue(asn.is_stale(entry([], fetched=now - asn.RETRY_AFTER, error="x"), now))
        self.assertFalse(asn.is_stale(entry([], fetched=now - asn.RETRY_AFTER + 1, error="x"), now))
        self.assertFalse(asn.is_stale(entry([], fetched=now - asn.RETRY_AFTER), now))

    def test_attempted(self):
        now = 1_000_000
        old_ok = dict(entry([], fetched=now - 10 * asn.REFRESH, error="x"), attempted=now - 60)
        self.assertFalse(asn.is_stale(old_ok, now))          # échec récent : pas de nouvel essai
        old_ok["attempted"] = now - asn.RETRY_AFTER
        self.assertTrue(asn.is_stale(old_ok, now))
        never = dict(entry([], fetched=None, error="x"), attempted=now - 60)
        self.assertFalse(asn.is_stale(never, now))


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="appalias-asn-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "asn.json")

    def test_absent(self):
        self.assertEqual(asn.load_cache(self.path), {"version": 1, "asns": {}})

    def test_unreadable(self):
        with open(self.path, "w") as fh:
            fh.write("{pas du json")
        self.assertEqual(asn.load_cache(self.path)["asns"], {})
        with open(self.path, "w") as fh:
            fh.write("[1, 2]")
        self.assertEqual(asn.load_cache(self.path)["asns"], {})

    def test_malformed_entries_ignored(self):
        data = {"version": 1, "asns": {"2906": {"prefixes": ["192.0.2.0/24", 5], "fetched": 12, "error": None},
                                       "bad": {"prefixes": [], "fetched": 1, "error": None},
                                       "15169": "x", "1": {"prefixes": "no"},
                                       "2": {"prefixes": [], "fetched": "z", "error": 3}}}
        with open(self.path, "w") as fh:
            json.dump(data, fh)
        cache = asn.load_cache(self.path)
        self.assertEqual(sorted(cache["asns"]), ["2", "2906"])
        self.assertEqual(cache["asns"]["2906"], entry(["192.0.2.0/24"], 12))
        self.assertEqual(cache["asns"]["2"], entry([], None))

    def test_roundtrip(self):
        data = {"version": 1, "asns": {"2906": entry(["192.0.2.0/24"], 5, "oups")}}
        asn.save_cache(data, self.path)
        self.assertEqual(asn.load_cache(self.path), data)
        self.assertEqual(os.listdir(self.dir), ["asn.json"])


class FetchTest(unittest.TestCase):
    def test_fetch(self):
        opener = FakeOpener()
        self.assertEqual(asn.fetch(2906, opener), ["198.51.100.0/24", "2001:db8::/32"])
        self.assertEqual(opener.calls, [URL])

    def test_dedup_and_invalid_items(self):
        body = json.dumps({"data": {"prefixes": [{"prefix": "192.0.2.0/24"}, {"prefix": "192.0.2.0/24"},
                                                 {"prefix": "nope"}, {"x": 1}, "str"]}}).encode()
        self.assertEqual(asn.fetch(1, FakeOpener(lambda n: body)), ["192.0.2.0/24"])

    def test_empty_list_is_valid(self):
        body = b'{"data": {"prefixes": []}}'
        self.assertEqual(asn.fetch(1, FakeOpener(lambda n: body)), [])

    def test_too_large(self):
        body = b" " * (asn.MAX_BODY + 1)
        with self.assertRaises(asn.AsnError):
            asn.fetch(1, FakeOpener(lambda n: body))

    def test_bad_json_and_shape(self):
        for body in (b"pas du json", b"[]", b'{"data": {}}', b'{"data": {"prefixes": "x"}}', b"\xff\xfe"):
            with self.assertRaises(asn.AsnError, msg=body):
                asn.fetch(1, FakeOpener(lambda n, b=body: b))

    def test_http_refused(self):
        with self.assertRaises(asn.AsnError):
            asn.http_get("http://stat.ripe.net/x", 100, FakeOpener())
        with self.assertRaises(asn.AsnError):
            asn.http_get("https://example.com/x", 100, FakeOpener())

    def test_redirect_other_host_refused(self):
        with self.assertRaises(asn.AsnError):
            asn.fetch(2906, FakeOpener(final_url="https://example.com/data.json"))

    def test_redirect_handler_refuses_other_host(self):
        handler = asn._SameHostsRedirect()
        with self.assertRaises(asn.AsnError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")

    def test_network_error(self):
        def boom(number):
            raise OSError("connexion coupée")
        with self.assertRaises(asn.AsnError):
            asn.fetch(1, FakeOpener(boom))


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="appalias-asn-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.config = os.path.join(self.dir, "config.json")
        self.cache = os.path.join(self.dir, "asn.json")
        self.reloads = []
        self.logged = []
        original = asn_update.log_error
        asn_update.log_error = self.logged.append
        self.addCleanup(setattr, asn_update, "log_error", original)
        self.write_config(CONFIG)

    def write_config(self, data):
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def run_update(self, opener, if_needed=False, reload=True, now=1_000_000):
        return asn_update.run(self.config, self.cache, if_needed=if_needed, reload=reload, opener=opener,
                              sleep=lambda s: None, now=now, configctl=["configctl-factice"])

    def fake_reload(self):
        # configctl remplacé : aucun sous-processus réel
        original = asn_update.reload_daemon
        asn_update.reload_daemon = lambda command=None: self.reloads.append(command)
        self.addCleanup(setattr, asn_update, "reload_daemon", original)

    def test_full_run(self):
        self.fake_reload()
        opener = FakeOpener()
        out = self.run_update(opener)
        self.assertEqual(out, {"status": "updated", "asns": 1, "prefixes": 2, "fetched": 1, "errors": {}})
        self.assertEqual(asn.load_cache(self.cache)["asns"]["2906"],
                         entry(["198.51.100.0/24", "2001:db8::/32"], 1_000_000))
        self.assertEqual(self.reloads, [["configctl-factice"]])

    def test_if_needed_unchanged_no_network(self):
        self.fake_reload()
        self.run_update(FakeOpener())
        self.reloads.clear()
        opener = FakeOpener()
        out = self.run_update(opener, if_needed=True, now=1_000_100)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(out["fetched"], 0)
        self.assertEqual(opener.calls, [])
        self.assertEqual(self.reloads, [])

    def test_if_needed_refetches_stale(self):
        self.fake_reload()
        self.run_update(FakeOpener())
        opener = FakeOpener()
        out = self.run_update(opener, if_needed=True, now=1_000_000 + asn.REFRESH)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(out["fetched"], 1)
        self.assertEqual(asn.load_cache(self.cache)["asns"]["2906"]["fetched"], 1_000_000 + asn.REFRESH)
        self.assertEqual(len(self.reloads), 1)  # celui du premier passage seulement

    def test_failure_keeps_old_prefixes(self):
        self.fake_reload()
        self.run_update(FakeOpener())
        self.reloads.clear()

        def boom(number):
            raise OSError("panne")
        out = self.run_update(FakeOpener(boom), now=1_000_500)
        self.assertEqual(out["status"], "unchanged")
        self.assertEqual(out["fetched"], 0)
        self.assertEqual(list(out["errors"]), ["2906"])
        stored = asn.load_cache(self.cache)["asns"]["2906"]
        self.assertEqual(stored["prefixes"], ["198.51.100.0/24", "2001:db8::/32"])
        self.assertIn("panne", stored["error"])
        self.assertEqual(stored["fetched"], 1_000_000)     # dernier succès conservé
        self.assertEqual(stored["attempted"], 1_000_500)
        self.assertEqual(self.reloads, [])

    def test_failure_first_time(self):
        def boom(number):
            raise OSError("panne")
        out = self.run_update(FakeOpener(boom), reload=False)
        self.assertEqual(out["status"], "updated")  # nouvel ASN dans le cache (sans préfixe)
        self.assertEqual(out["asns"], 1)
        self.assertEqual(out["prefixes"], 0)
        self.assertIn("2906", out["errors"])

    def test_no_reload(self):
        self.fake_reload()
        out = self.run_update(FakeOpener(), reload=False)
        self.assertEqual(out["status"], "updated")
        self.assertEqual(self.reloads, [])

    def test_unneeded_asn_removed(self):
        self.fake_reload()
        asn.save_cache({"version": 1, "asns": {"2906": entry(["198.51.100.0/24", "2001:db8::/32"], 1_000_000),
                                               "99": entry(["203.0.113.0/24"])}}, self.cache)
        out = self.run_update(FakeOpener(), if_needed=True, now=1_000_100)
        self.assertEqual(out["status"], "updated")
        self.assertEqual(sorted(asn.load_cache(self.cache)["asns"]), ["2906"])

    def test_pause_between_calls(self):
        self.write_config({"general": {"enabled": True, "upstream_address": "192.0.2.53"},
                           "apps": [{"name": "A", "domains": [], "asns": ["AS1", "AS2", "AS3"]}]})
        pauses = []
        asn_update.run(self.config, self.cache, opener=FakeOpener(), sleep=pauses.append, reload=False)
        self.assertEqual(pauses, [asn_update.PAUSE, asn_update.PAUSE])

    def test_disabled(self):
        cfg = dict(CONFIG, general={"enabled": True, "upstream_address": "192.0.2.53",
                                    "asn_enabled": False})
        self.write_config(cfg)
        opener = FakeOpener()
        out = self.run_update(opener)
        self.assertEqual(out, {"status": "disabled"})
        self.assertEqual(opener.calls, [])
        self.assertFalse(os.path.exists(self.cache))

    def test_plugin_desactive(self):
        # plugin désactivé : jamais de connexion à RIPEstat, même avec asn_enabled
        cfg = dict(CONFIG, general={"enabled": False, "upstream_address": "192.0.2.53",
                                    "asn_enabled": True})
        self.write_config(cfg)
        opener = FakeOpener()
        self.assertEqual(self.run_update(opener), {"status": "disabled"})
        self.assertEqual(self.run_update(opener, if_needed=True), {"status": "disabled"})
        self.assertEqual(opener.calls, [])
        self.assertFalse(os.path.exists(self.cache))

    def test_busy(self):
        with asn_update.Lock(os.path.join(self.dir, asn_update.LOCK_NAME)):
            opener = FakeOpener()
            out = self.run_update(opener)
        self.assertEqual(out, {"status": "busy"})
        self.assertEqual(opener.calls, [])

    def test_main_json_and_exit_codes(self):
        out = io.StringIO()
        from contextlib import redirect_stdout
        with redirect_stdout(out):
            code = asn_update.main(["update", "--config", self.config, "--cache", self.cache, "--no-reload"],
                                   opener=FakeOpener(), sleep=lambda s: None)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "updated")
        out = io.StringIO()
        with redirect_stdout(out):
            code = asn_update.main(["update", "--config", os.path.join(self.dir, "absent.json"),
                                    "--cache", self.cache], opener=FakeOpener())
        self.assertEqual(code, 1)

    def test_config_error_raised(self):
        with self.assertRaises(cfgmod.ConfigError):
            asn_update.run(os.path.join(self.dir, "absent.json"), self.cache)


if __name__ == "__main__":
    unittest.main()
