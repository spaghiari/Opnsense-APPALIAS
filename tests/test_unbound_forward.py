import contextlib
import io
import json
import os
import tempfile
import unittest

import appalias_testutil  # noqa: F401

import unbound_forward
from lib import config as cfgmod
from lib import unboundfwd as ub

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "unbound", "config.json")
ZONES = ["d1.cloudfront.net", "edge.cloudfront.net", "parent.example", "ttvnw.net", "twitch.tv"]


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Restarter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.ok, "OK" if self.ok else "Error (1)"


class PlanTests(unittest.TestCase):
    def plan(self, data):
        return ub.plan(cfgmod.from_dict(data))

    def test_fixture_plan(self):
        zones, conflicts, active = self.plan(load_fixture())
        self.assertTrue(active)
        self.assertEqual(zones, ZONES)
        reasons = {}
        for c in conflicts:
            reasons.setdefault(c["name"], (c["reason"], c["detail"]))
        self.assertEqual(reasons["dot.example"], ("unbound_zone", "dot.example"))          # DoT
        self.assertEqual(reasons["fwd.example"], ("unbound_zone", "fwd.example"))          # forward désactivé
        self.assertEqual(reasons["sub.dot.example"], ("unbound_covered", "dot.example"))
        self.assertEqual(reasons["www.twitch.tv"], ("merged", "twitch.tv"))               # parent/enfant
        self.assertEqual(reasons["api.twitch.tv"], ("merged", "twitch.tv"))               # full: fusionné
        self.assertEqual(reasons["printer.home.arpa"], ("local", "home.arpa"))
        self.assertEqual(reasons["nas.stratos.lan"], ("local", "stratos.lan"))           # domaine système
        self.assertEqual(reasons["router.example.org"], ("local", "router.example.org"))  # host override
        self.assertEqual(reasons["cloudfront.net"][0], "shared")
        self.assertEqual(reasons["amazonaws.com"][0], "shared")                          # full: plateforme
        self.assertEqual(reasons["com"][0], "tld")                                       # full:com
        self.assertNotIn("disabled.example", reasons)
        twitch = [c for c in conflicts if c["name"] == "www.twitch.tv"][0]
        self.assertEqual(twitch["apps"], ["TWITCH"])

    def test_full_on_shared_platform(self):
        data = load_fixture()
        data["apps"] = [{"name": "A", "domains": ["full:cloudfront.net", "full:gstatic.com",
                                                  "full:net", "full:d9.cloudfront.net"]}]
        zones, conflicts, _a = self.plan(data)
        self.assertEqual(zones, ["d9.cloudfront.net"])
        self.assertEqual(sorted(c["reason"] for c in conflicts), ["shared", "shared", "tld"])

    def test_no_duplicate_with_unbound(self):
        data = load_fixture()
        zones, _c, _a = self.plan(data)
        existing = {d["domain"].rstrip(".") for d in data["unbound"]["dots"] if d["domain"]}
        self.assertFalse(existing & set(zones))
        self.assertEqual(len(zones), len(set(zones)))

    def test_parent_added_later_merges_children(self):
        data = load_fixture()
        data["apps"] = [{"name": "A", "domains": ["a.b.example", "b.example", "c.a.b.example"]},
                        {"name": "B", "domains": ["x.example"]}]
        zones, conflicts, _a = self.plan(data)
        self.assertEqual(zones, ["b.example", "x.example"])
        self.assertEqual(sorted(c["name"] for c in conflicts if c["reason"] == "merged"),
                         ["a.b.example", "c.a.b.example"])

    def test_opt_in(self):
        data = load_fixture()
        del data["general"]["unbound_autoforward"]   # config 0.2 : désactivé
        self.assertEqual(self.plan(data), ([], [], False))
        data["general"]["unbound_autoforward"] = ""
        self.assertEqual(self.plan(data), ([], [], False))
        data["general"]["unbound_autoforward"] = "1"
        self.assertTrue(self.plan(data)[2])
        data["general"]["enabled"] = False
        self.assertEqual(self.plan(data), ([], [], False))

    def test_loop_detection(self):
        for upstream, port, loop in (("127.0.0.1", 53, True),
                                     ("::1", 53, True),
                                     ("192.168.1.1", 53, True),      # IP d'interface
                                     ("2001:db8::1", 53, True),
                                     ("10.10.10.1", 5353, False),    # autre port
                                     ("192.0.2.53", 53, False)):
            data = load_fixture()
            data["general"]["upstream_address"] = upstream
            data["general"]["upstream_port"] = port
            zones, conflicts, active = self.plan(data)
            self.assertTrue(active)
            self.assertEqual(not zones, loop, (upstream, port))
            self.assertEqual(any(c["reason"] == "loop" for c in conflicts), loop, (upstream, port))
        data = load_fixture()
        data["unbound"]["port"] = 5353
        data["general"]["upstream_address"] = "10.10.10.1"
        data["general"]["upstream_port"] = 5353          # port d'Unbound
        self.assertEqual(self.plan(data)[0], [])
        data = load_fixture()
        data["general"]["upstream_fallback"] = ["192.168.1.1:53"]   # amont de secours
        self.assertEqual(self.plan(data)[0], [])

    def test_names_revalidated(self):
        data = load_fixture()
        data["apps"] = [{"name": "A", "domains": ["ok.example", "bad..example", "x\"y.example",
                                                  "under_score.example", "a" * 64 + ".example"]}]
        zones, conflicts, _a = self.plan(data)
        self.assertEqual(zones, ["ok.example", "under_score.example"])
        self.assertEqual(len([c for c in conflicts if c["reason"] == "invalid"]), 3)
        text = ub.render(["ok.example", "evil\"\n  forward-addr: 6.6.6.6", ""], "127.0.0.1", 53535)
        self.assertEqual(ub.parse_zones(text), ["ok.example"])
        self.assertNotIn("6.6.6.6", text)

    def test_render(self):
        text = ub.render(["twitch.tv", "ttvnw.net"], "127.0.0.1", 53535)
        self.assertIn('forward-zone:\n  name: "twitch.tv."\n  forward-addr: 127.0.0.1@53535\n'
                      '  forward-first: yes\n', text)
        self.assertEqual(text.count("forward-first: yes"), 2)
        self.assertIn("server:\n  do-not-query-localhost: no\n", text)
        self.assertEqual(ub.parse_zones(text), ["twitch.tv", "ttvnw.net"])
        other = ub.render(["twitch.tv"], "192.168.1.1", 53535)
        self.assertIn("server:\n", other)
        self.assertNotIn("do-not-query-localhost", other)
        self.assertIn("forward-addr: 192.168.1.1@53535", other)
        self.assertEqual(ub.render([], "127.0.0.1", 53535), "")

    def test_restart_command(self):
        calls = []

        class Proc:
            returncode = 0
            stdout = "OK\n"
            stderr = ""

        def runner(argv, **kw):
            calls.append(argv)
            return Proc()

        self.assertEqual(ub.restart_unbound(runner), (True, "OK"))
        self.assertEqual(calls, [["/usr/local/sbin/configctl", "unbound", "restart"]])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = os.path.join(self.tmp.name, "config.json")
        self.conf = os.path.join(self.tmp.name, "unbound.opnsense.d", "appalias.conf")
        self.runtime_dir = os.path.join(self.tmp.name, "var-unbound-etc")
        os.mkdir(self.runtime_dir)
        self.runtime = os.path.join(self.runtime_dir, "appalias.conf")
        self.status = os.path.join(self.tmp.name, "unbound.json")
        self.write(load_fixture())

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        with open(self.config, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def sync(self, apply=True, restarter=None):
        return unbound_forward.sync(self.config, self.conf, self.runtime, self.status,
                                    apply=apply, restarter=restarter)

    def test_restart_only_when_changed(self):
        restarter = Restarter()
        code, status = self.sync(restarter=restarter)
        self.assertEqual(code, 0)
        self.assertEqual(restarter.calls, 1)
        self.assertTrue(status["restarted"])
        self.assertEqual(status["zones"], ZONES)
        with open(self.conf, encoding="utf-8") as fh:
            text = fh.read()
        with open(self.runtime, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text)
        # Même contenu : pas de redémarrage.
        code, status = self.sync(restarter=restarter)
        self.assertEqual((code, restarter.calls, status["changed"]), (0, 1, False))
        # Nouveau domaine : redémarrage.
        data = load_fixture()
        data["apps"][1]["domains"].append("new.example")
        self.write(data)
        self.sync(restarter=restarter)
        self.assertEqual(restarter.calls, 2)
        # Port du relais changé : contenu changé, redémarrage.
        data["general"]["listen_port"] = 53536
        self.write(data)
        self.sync(restarter=restarter)
        self.assertEqual(restarter.calls, 3)

    def test_write_only_never_restarts(self):
        restarter = Restarter()
        code, status = self.sync(apply=False, restarter=restarter)
        self.assertEqual((code, restarter.calls), (0, 0))
        self.assertTrue(status["changed"])
        self.assertTrue(os.path.exists(self.conf))

    def test_unbound_disabled_no_restart(self):
        data = load_fixture()
        data["unbound"]["enabled"] = False
        self.write(data)
        restarter = Restarter()
        self.sync(restarter=restarter)
        self.assertEqual(restarter.calls, 0)
        self.assertTrue(os.path.exists(self.conf))

    def test_disable_removes_and_restarts_once(self):
        restarter = Restarter()
        self.sync(restarter=restarter)
        data = load_fixture()
        data["general"]["unbound_autoforward"] = False
        self.write(data)
        code, status = self.sync(restarter=restarter)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.conf))
        self.assertFalse(os.path.exists(self.runtime))
        self.assertFalse(status["active"])
        self.assertEqual(restarter.calls, 2)
        self.sync(restarter=restarter)              # déjà vide : rien
        self.assertEqual(restarter.calls, 2)

    def test_restart_failure(self):
        code, status = self.sync(restarter=Restarter(ok=False))
        self.assertEqual(code, 1)
        self.assertFalse(status["restarted"])
        self.assertTrue(status["errors"])
        self.assertTrue(os.path.exists(self.conf))       # persistance pour le prochain démarrage
        with open(self.status, encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["errors"])

    def test_restart_pending_retried(self):
        code, status = self.sync(restarter=Restarter(ok=False))
        self.assertEqual(code, 1)
        self.assertTrue(status["restart_pending"])
        # Contenu inchangé, mais redémarrage en attente : relancé.
        retry = Restarter()
        code, status = self.sync(restarter=retry)
        self.assertEqual((code, retry.calls), (0, 1))
        self.assertFalse(status["changed"])
        self.assertFalse(status["restart_pending"])
        # Drapeau remis à false : plus de redémarrage sans changement.
        self.sync(restarter=retry)
        self.assertEqual(retry.calls, 1)
        # Écriture seule : le drapeau est conservé, sans redémarrage.
        data = load_fixture()
        data["apps"][1]["domains"].append("other.example")
        self.write(data)
        self.assertTrue(self.sync(restarter=Restarter(ok=False))[1]["restart_pending"])
        code, status = self.sync(apply=False, restarter=retry)
        self.assertTrue(status["restart_pending"])
        self.assertEqual(retry.calls, 1)

    def test_root_forward_warning(self):
        _code, status = self.sync(restarter=Restarter())
        self.assertTrue(status["root_forward"])          # dots : domaine vide, dot, activé
        data = load_fixture()
        data["unbound"]["dots"][0]["enabled"] = False
        self.write(data)
        _code, status = self.sync(restarter=Restarter())
        self.assertFalse(status["root_forward"])
        data["unbound"]["dots"].append({"domain": "", "type": "forward", "enabled": True})
        self.write(data)
        _code, status = self.sync(restarter=Restarter())
        self.assertTrue(status["root_forward"])

    def test_loop_reported(self):
        data = load_fixture()
        data["general"]["upstream_address"] = "10.10.10.1"
        self.write(data)
        code, status = self.sync(restarter=Restarter())
        self.assertEqual(code, 0)
        self.assertEqual(status["zones"], [])
        self.assertTrue(any("loop" in e for e in status["errors"]))
        self.assertFalse(os.path.exists(self.conf))

    def test_bad_config(self):
        os.unlink(self.config)
        code, status = self.sync(restarter=Restarter())
        self.assertEqual(code, 1)
        self.assertTrue(status["errors"])

    def test_cli_usage(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(unbound_forward.main(["--bogus"]), 64)


if __name__ == "__main__":
    unittest.main()
