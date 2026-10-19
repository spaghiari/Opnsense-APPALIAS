"""Suivi automatique du catalogue (lib/follow.py, catalog_follow.py) : ajouts et
retraits, règles ajoutées à la main préservées, garde-fou, plafond, journal
borné, annulation, et branchement après une mise à jour du catalogue.

Toute la logique du suivi est en Python (lib/follow.py) ; catalog_follow.php
ne fait que lire et enregistrer le modèle sous verrou, et lui passer les
listes en JSON : ces tests couvrent donc le calcul réellement exécuté."""

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import appalias_testutil  # noqa: F401

import catalog
import catalog_follow
import catalog_update
from lib import follow
from lib import v2fly

UUID = "11111111-0000-4000-8000-000000000001"
OTHER = "22222222-0000-4000-8000-000000000002"
PLATFORMS = frozenset(["cloudfront.net", "akamaized.net"])

LST = (
    "[stream]\n"
    "stream.example\n"
    "full:api.stream.example\n"
    "newcdn.example\n"
    "domain:cloudfront.net\n"
    "full:akamaized.net\n"
    "keyword:stream\n"
    "ads.stream.example @ads\n"
    "[other]\n"
    "other.example\n"
)


def items(*rules):
    """[(kind, value, attrs)] à partir de textes de règles."""
    out = []
    for rule in rules:
        kind, value = rule.split(":", 1)
        out.append((kind, value, frozenset()))
    return out


class PlanTests(unittest.TestCase):
    def plan(self, resolved, catalog_rules, domains, declined=(), ignored=None, cap=follow.MAX_APP_RULES):
        return follow.plan_app(items(*resolved), catalog_rules, domains, declined, PLATFORMS,
                               ignored, cap)

    def test_add_and_remove(self):
        p = self.plan(["domain:a.example", "domain:b.example", "full:new.example"],
                      ["domain:a.example", "domain:gone.example"],
                      ["domain:a.example", "domain:gone.example"])
        self.assertEqual(p["add"], ["domain:b.example", "full:new.example"])
        self.assertEqual(p["remove"], ["domain:gone.example"])
        self.assertEqual(p["removed"], ["domain:gone.example"])
        self.assertEqual(p["domains"], ["domain:a.example", "domain:b.example", "full:new.example"])
        self.assertEqual(p["catalog_rules"], ["domain:a.example", "domain:b.example", "full:new.example"])
        self.assertTrue(p["changed"])
        self.assertFalse(p["capped"])

    def test_manual_rules_never_touched(self):
        # « full:mine.example » et « domain:b.example » ont été ajoutées à la main
        p = self.plan(["domain:a.example"], ["domain:a.example", "domain:old.example"],
                      ["full:mine.example", "domain:a.example", "domain:old.example", "domain:b.example"])
        self.assertEqual(p["remove"], ["domain:old.example"])
        self.assertEqual(p["domains"], ["full:mine.example", "domain:a.example", "domain:b.example"])
        # une règle du catalogue déjà saisie à la main (même sous une autre forme) n'est pas reprise
        p = self.plan(["domain:a.example", "domain:b.example"], ["domain:a.example"],
                      ["domain:a.example", "B.Example."])
        self.assertEqual(p["add"], [])
        self.assertFalse(p["changed"])
        self.assertEqual(p["domains"], ["domain:a.example", "B.Example."])

    def test_removed_rule_already_deleted_by_hand(self):
        p = self.plan(["domain:a.example"], ["domain:a.example", "domain:old.example"], ["domain:a.example"])
        self.assertEqual(p["remove"], ["domain:old.example"])
        self.assertEqual(p["removed"], [])              # absente de domains : rien à journaliser
        self.assertEqual(p["catalog_rules"], ["domain:a.example"])
        self.assertTrue(p["changed"])

    def test_no_change(self):
        p = self.plan(["domain:a.example"], ["domain:a.example"], ["domain:a.example"])
        self.assertFalse(p["changed"])
        self.assertEqual((p["add"], p["remove"]), ([], []))

    def test_guard_and_skipped_rules(self):
        ignored = follow.Ignored(["domain:noisy.example", "full:one.example", "bogus"])
        p = self.plan(["domain:cloudfront.net", "domain:com", "full:d1.cloudfront.net", "full:cloudfront.net",
                       "keyword:stream", "regexp:^x$", "domain:a.noisy.example", "full:one.example",
                       "domain:ok.example"],
                      [], [], ignored=ignored)
        # sous-domaine dédié d'une plateforme : ajouté ; plateforme elle-même : sautée
        self.assertEqual(sorted(p["add"]), ["domain:ok.example", "full:d1.cloudfront.net"])
        self.assertEqual(p["skipped"]["refused"], ["domain:cloudfront.net", "domain:com"])
        self.assertEqual(p["skipped"]["shared"], ["full:cloudfront.net"])
        self.assertEqual(p["skipped"]["discovery"], ["keyword:stream", "regexp:^x$"])
        self.assertEqual(p["skipped"]["ignored"], ["domain:a.noisy.example", "full:one.example"])

    def test_tld_full_skipped(self):
        # « tld » exclu du suivi comme « shared » (uniformité avec proactive.py)
        p = self.plan(["full:com", "domain:ok.example"], [], [])
        self.assertEqual(p["add"], ["domain:ok.example"])
        self.assertEqual(p["skipped"]["shared"], ["full:com"])

    def test_comma_skipped(self):
        p = follow.plan_app([("regexp", "^a{1,2}$", frozenset()), ("domain", "x.example", frozenset())],
                            [], [], (), PLATFORMS)
        self.assertEqual(p["add"], ["domain:x.example"])
        self.assertEqual(p["skipped"]["comma"], ["regexp:^a{1,2}$"])

    def test_declined_rules_not_added(self):
        p = self.plan(["domain:a.example", "full:unchecked.example"], ["domain:a.example"],
                      ["domain:a.example"], declined=["full:unchecked.example"])
        self.assertFalse(p["changed"])

    def test_cap(self):
        domains = ["full:h%d.example" % i for i in range(999)]
        p = self.plan(["domain:x.example", "domain:y.example"], [], domains, cap=1000)
        self.assertTrue(p["capped"])
        self.assertFalse(p["changed"])
        self.assertEqual(p["domains"], domains)        # rien n'est appliqué
        self.assertEqual(p["count"], 1001)
        p = self.plan(["domain:x.example"], [], domains, cap=1000)
        self.assertFalse(p["capped"])
        self.assertTrue(p["changed"])

    def test_rule_key(self):
        self.assertEqual(follow.rule_key("Example.COM."), "domain:example.com")
        self.assertEqual(follow.rule_key("FULL:www.Example.com"), "full:www.example.com")
        self.assertEqual(follow.rule_key("regexp:^A$"), "regexp:^A$")


class CliPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "catalog")
        os.mkdir(self.dir)
        raw = LST.encode("ascii")
        with open(os.path.join(self.dir, "v2fly.lst"), "wb") as fh:
            fh.write(raw)
        with open(os.path.join(self.dir, "v2fly.manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"commit": "c" * 40, "date": "2026-10-17T00:00:00Z",
                       "lst_sha256": hashlib.sha256(raw).hexdigest()}, fh)
        self.store = catalog.Store(self.dir, os.path.join(self.tmp.name, "index.json"))
        self.journal = os.path.join(self.tmp.name, "catalog_follow.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv, data):
        return catalog_follow.run(argv, json.dumps(data), store=self.store, journal_path=self.journal)

    def test_plan_with_ads_filter(self):
        app = {"uuid": UUID, "name": "STREAM", "refs": ["stream@-ads"],
               "catalog_rules": ["domain:stream.example", "domain:old.example"],
               "domains": ["domain:stream.example", "domain:old.example", "full:mine.example"],
               "declined": ["full:api.stream.example"]}
        out = self.run_cli(["plan"], {"apps": [app], "ignored": [], "shared_extra": ["akamaized.net"],
                                      "cap": 1000})
        self.assertEqual(out["revision"], "c" * 40)
        p = out["apps"][UUID]
        self.assertEqual(p["add"], ["domain:newcdn.example"])       # @ads exclu par la référence
        self.assertEqual(p["remove"], ["domain:old.example"])
        self.assertEqual(p["domains"], ["domain:stream.example", "full:mine.example", "domain:newcdn.example"])
        self.assertEqual(p["skipped"]["refused"], ["domain:cloudfront.net"])
        self.assertEqual(p["skipped"]["shared"], ["full:akamaized.net"])   # shared_extra pris en compte
        self.assertEqual(p["skipped"]["discovery"], ["keyword:stream"])

    def test_plan_unknown_list_only_affects_its_app(self):
        apps = [{"uuid": UUID, "name": "A", "refs": ["vanished"], "catalog_rules": [], "domains": ["x.example"]},
                {"uuid": OTHER, "name": "B", "refs": ["other"], "catalog_rules": [], "domains": []}]
        out = self.run_cli(["plan"], {"apps": apps})
        self.assertIn("unknown list", out["apps"][UUID]["error"])
        self.assertEqual(out["apps"][OTHER]["add"], ["domain:other.example"])

    def test_plan_missing_include_applies_nothing(self):
        raw = (LST + "[broken]\ninclude:vanished\nbroken.example\n").encode("ascii")
        with open(os.path.join(self.dir, "v2fly.lst"), "wb") as fh:
            fh.write(raw)
        with open(os.path.join(self.dir, "v2fly.manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"commit": "c" * 40, "date": "2026-10-17T00:00:00Z",
                       "lst_sha256": hashlib.sha256(raw).hexdigest()}, fh)
        app = {"uuid": UUID, "name": "B", "refs": ["broken"], "catalog_rules": ["domain:gone.example"],
               "domains": ["domain:gone.example"]}
        out = self.run_cli(["plan"], {"apps": [app, {"uuid": OTHER, "name": "O", "refs": ["other"],
                                                      "catalog_rules": [], "domains": []}]})
        self.assertIn("catalog list incomplete", out["apps"][UUID]["error"])
        self.assertNotIn("add", out["apps"][UUID])
        self.assertEqual(out["apps"][OTHER]["add"], ["domain:other.example"])

    def test_undo_plan_uses_journal(self):
        first = self.run_cli(["record"], {"entries": [{
            "app_uuid": UUID, "app": "S", "added": ["domain:newcdn.example"], "removed": [], "kind": "follow"}]})["ids"][0]
        self.run_cli(["record"], {"entries": [{
            "app_uuid": UUID, "app": "S", "added": ["domain:newcdn.example"], "removed": [], "kind": "follow"}]})
        state = {"domains": ["domain:newcdn.example"], "catalog_rules": ["domain:newcdn.example"], "declined": []}
        out = self.run_cli(["undo-plan"], {"entry_id": first, "apps": {UUID: state}})
        self.assertIn("a later change touches the same rules", out["error"])

    def test_plan_rejects_bad_input(self):
        self.assertIn("error", self.run_cli(["plan"], {"apps": "x"}))
        self.assertIn("error", self.run_cli(["plan"], {"apps": [{"uuid": "../x"}]}))
        self.assertIn("error", self.run_cli(["plan"], {"apps": [], "cap": 0}))
        self.assertIn("error", catalog_follow.run(["plan"], "not json", store=self.store))
        self.assertIn("error", catalog_follow.run(["nope"], "{}"))

    def test_record_log_and_undo_plan(self):
        ids = self.run_cli(["record"], {"entries": [{
            "app_uuid": UUID, "app": "STREAM", "added": ["domain:newcdn.example"],
            "removed": ["domain:old.example"], "revision": "c" * 40, "kind": "follow"}]})["ids"]
        self.assertEqual(len(ids), 1)
        log = catalog_follow.run(["log"], journal_path=self.journal)
        self.assertEqual(log["entries"][0]["id"], ids[0])
        state = {"domains": ["domain:stream.example", "domain:newcdn.example"],
                 "catalog_rules": ["domain:stream.example", "domain:newcdn.example"], "declined": []}
        plan = self.run_cli(["undo-plan"], {"entry_id": ids[0], "apps": {UUID: state}})
        self.assertEqual(plan["domains"], ["domain:stream.example", "domain:old.example"])
        self.assertEqual(plan["catalog_rules"], ["domain:stream.example"])
        self.assertEqual(plan["catalog_declined"], ["domain:newcdn.example"])
        self.assertEqual((plan["added"], plan["removed"]), (["domain:old.example"], ["domain:newcdn.example"]))
        # application supprimée depuis : refus
        out = self.run_cli(["undo-plan"], {"entry_id": ids[0], "apps": []})
        self.assertIn("deleted", out["error"])
        # entrée inconnue, identifiant invalide
        self.assertIn("error", self.run_cli(["undo-plan"], {"entry_id": "0" * 16, "apps": {}}))
        self.assertIn("error", self.run_cli(["undo-plan"], {"entry_id": "x; rm", "apps": {}}))
        self.assertIn("error", catalog_follow.run(["log", "0"], journal_path=self.journal))


class UndoTests(unittest.TestCase):
    def test_inverse_and_persistence(self):
        entry = {"id": "a" * 16, "added": ["domain:new.example"], "removed": ["domain:old.example"]}
        plan = follow.plan_undo(entry, ["full:mine.example", "domain:keep.example", "domain:new.example"],
                                ["domain:keep.example", "domain:new.example"], ["full:x.example"])
        self.assertEqual(plan["domains"], ["full:mine.example", "domain:keep.example", "domain:old.example"])
        # la règle remise n'est pas dans catalog_rules : le prochain suivi ne la retire pas
        self.assertEqual(plan["catalog_rules"], ["domain:keep.example"])
        # la règle annulée est refusée : le prochain suivi ne la remet pas
        self.assertEqual(plan["catalog_declined"], ["full:x.example", "domain:new.example"])
        nxt = follow.plan_app(items("domain:keep.example", "domain:new.example"), plan["catalog_rules"],
                              plan["domains"], plan["catalog_declined"], PLATFORMS)
        self.assertFalse(nxt["changed"])

    def test_undo_of_undo_refused(self):
        undo = {"id": "b" * 16, "kind": "undo", "undo_of": "a" * 16,
                "added": ["domain:old.example"], "removed": ["domain:new.example"]}
        with self.assertRaises(follow.FollowError) as ctx:
            follow.plan_undo(undo, ["domain:keep.example", "domain:old.example"], ["domain:keep.example"],
                             ["domain:new.example"])
        self.assertEqual(str(ctx.exception), "cannot undo an undo")

    def test_later_change_on_same_rules_refused(self):
        def ent(i, app, added, **extra):
            return dict({"id": "%016x" % i, "app_uuid": app, "kind": "follow", "added": added,
                         "removed": [], "undone_by": None}, **extra)

        first = ent(1, UUID, ["domain:new.example"])
        journal = [first, ent(2, UUID, ["Domain:NEW.example"])]      # même règle, casse différente
        with self.assertRaises(follow.FollowError) as ctx:
            follow.plan_undo(first, ["domain:new.example"], ["domain:new.example"], [], journal=journal)
        self.assertEqual(str(ctx.exception), "a later change touches the same rules: undo it first")
        # entrée plus récente annulée, autre application, autres règles ou plus ancienne : autorisé
        for others in ([ent(2, UUID, ["domain:new.example"], undone_by="c" * 16)],
                       [ent(2, OTHER, ["domain:new.example"])],
                       [ent(2, UUID, ["domain:else.example"])]):
            plan = follow.plan_undo(first, ["domain:new.example"], ["domain:new.example"], [],
                                    journal=[first] + others)
            self.assertEqual(plan["domains"], [])
        older = ent(0, UUID, ["domain:new.example"])
        follow.plan_undo(first, ["domain:new.example"], ["domain:new.example"], [], journal=[older, first])

    def test_refusals(self):
        with self.assertRaises(follow.FollowError):
            follow.plan_undo({"id": "a" * 16, "undone_by": "b" * 16, "added": [], "removed": []}, [], [], [])
        entry = {"id": "a" * 16, "added": [], "removed": ["domain:r%d.example" % i for i in range(3)]}
        with self.assertRaises(follow.FollowError):
            follow.plan_undo(entry, ["full:x.example"], [], [], cap=3)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "catalog_follow.json")

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, i, **extra):
        return dict({"app_uuid": UUID, "app": "APP%d" % i, "added": ["full:a%d.example" % i],
                     "removed": [], "revision": "r", "kind": "follow"}, **extra)

    def test_bounded_and_ordered(self):
        for i in range(follow.JOURNAL_MAX + 15):
            follow.record([self.entry(i)], self.path, now=1000 + i)
        journal = follow.load_journal(self.path)
        self.assertEqual(len(journal), follow.JOURNAL_MAX)
        self.assertEqual(journal[0]["app"], "APP15")          # les plus anciennes sont sorties
        self.assertEqual(len({e["id"] for e in journal}), follow.JOURNAL_MAX)
        recent = follow.recent(self.path)
        self.assertEqual(len(recent["entries"]), follow.LOG_DEFAULT)
        self.assertEqual(recent["entries"][0]["app"], "APP%d" % (follow.JOURNAL_MAX + 14))
        self.assertEqual(recent["total"], follow.JOURNAL_MAX)
        for entry in journal:
            self.assertRegex(entry["id"], follow.ENTRY_ID_RE)

    def test_undo_marks_entry(self):
        first = follow.record([self.entry(1)], self.path, now=10)[0]
        second = follow.record([self.entry(2, undo_of=first, kind="follow")], self.path, now=20)[0]
        by_id = {e["id"]: e for e in follow.load_journal(self.path)}
        self.assertEqual(by_id[first]["undone_by"], second)
        self.assertEqual(by_id[second]["kind"], "undo")
        self.assertEqual(by_id[second]["undo_of"], first)
        with self.assertRaises(follow.FollowError):
            follow.plan_undo(by_id[first], [], [], [])

    def test_invalid_entries(self):
        with self.assertRaises(follow.FollowError):
            follow.record([{"app_uuid": "nope"}], self.path)
        with self.assertRaises(follow.FollowError):
            follow.record([dict(self.entry(1), added=["x," * 600])], self.path)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("garbage")
        self.assertEqual(follow.load_journal(self.path), [])
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"entries": [{"id": "zz"}, dict(self.entry(1), id="c" * 16, time=5)]}, fh)
        self.assertEqual([e["id"] for e in follow.load_journal(self.path)], ["c" * 16])


class CatalogUpdateHookTests(unittest.TestCase):
    def test_follow_after_update_only(self):
        calls = []

        def fake_follow():
            calls.append(1)
            return {"result": "updated", "changed": 1}

        with mock.patch.object(catalog_update, "update", return_value={"result": "updated"}), \
                mock.patch("builtins.print") as printed:
            catalog_update.main(["update"], follow=fake_follow)
        out = json.loads(printed.call_args[0][0])
        self.assertEqual(out["follow"], {"result": "updated", "changed": 1})
        self.assertEqual(len(calls), 1)
        for result in ("unchanged", "failed"):
            with mock.patch.object(catalog_update, "update", return_value={"result": result}), \
                    mock.patch("builtins.print") as printed:
                catalog_update.main(["update"], follow=fake_follow)
            self.assertNotIn("follow", json.loads(printed.call_args[0][0]))
        self.assertEqual(len(calls), 1)

    def test_run_follow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(catalog_update.run_follow(os.path.join(tmp, "absent.php")))
            script = os.path.join(tmp, "catalog_follow.php")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("")
            seen = []

            def run(argv, **kw):
                seen.append((argv, kw))

            # détaché : daemon -f, rend la main, le suivi lui-même n'a pas de timeout
            self.assertEqual(catalog_update.run_follow(script, run=run, daemon="/d"), {"result": "started"})
            self.assertEqual(seen[0][0], ["/d", "-f", script, "run"])
            self.assertLessEqual(seen[0][1]["timeout"], 60)

            def failing(argv, **kw):
                raise OSError("no daemon")

            out = catalog_update.run_follow(script, run=failing)
            self.assertEqual(out["result"], "failed")


class ImportDefaultsTests(unittest.TestCase):
    """Les règles ajoutées par le suivi suivent les cases cochées par défaut à
    l'import : mêmes critères que catalog.py preview (shared, refused,
    discovery)."""

    def test_same_criteria_as_preview(self):
        cat = v2fly.Catalog.parse(LST)
        res = cat.resolve_refs(["stream"])
        p = follow.plan_app(res.items(), [], [], (), PLATFORMS)
        self.assertEqual(p["add"], ["domain:ads.stream.example", "domain:newcdn.example",
                                    "domain:stream.example", "full:api.stream.example"])
        skipped = sorted(r for rules in p["skipped"].values() for r in rules)
        self.assertEqual(skipped, ["domain:cloudfront.net", "full:akamaized.net", "keyword:stream"])


if __name__ == "__main__":
    unittest.main()
