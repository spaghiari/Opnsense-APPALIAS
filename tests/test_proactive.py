import logging
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

import appalias_testutil  # noqa: F401

from lib import proactive
from lib.dnswire import TYPE_A, TYPE_AAAA
from lib.matcher import Matcher
from lib.proactive import QTYPE_WILDCARD, Scheduler, targets

PLATFORMS = frozenset({"cloudfront.net"})
QUIET = logging.getLogger("appalias.test.proactive")
QUIET.addHandler(logging.NullHandler())


def app(table, rules, enabled=True, proactive_=True, families="both", refs=()):
    return SimpleNamespace(table=table, domains=list(rules), enabled=enabled,
                           proactive=proactive_, families=families, catalog_refs=list(refs))


def build(apps, prefixes=("www",), deep=True):
    cfg = SimpleNamespace(apps=apps, proactive_prefixes=list(prefixes), proactive_deep=deep)
    matcher = Matcher({a.table: a.domains for a in apps if a.enabled},
                      logger=QUIET, platforms=PLATFORMS)
    return cfg, matcher


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def capture(name):
    logger = logging.getLogger(name)
    logger.propagate = False
    handler = ListHandler()
    logger.addHandler(handler)
    return logger, handler


class TargetsTests(unittest.TestCase):
    def run_targets(self, apps, prefixes=("www",), logger=None):
        cfg, matcher = build(apps, prefixes)
        return targets(cfg, matcher, PLATFORMS, logger=logger or QUIET)

    def test_full_et_domain_avec_prefixes(self):
        t = self.run_targets([app("A", ["full:api.example.com", "domain:example.org"])],
                             prefixes=("www", "m"))
        names = {n for n, _ in t}
        self.assertEqual(names, {"api.example.com", "example.org",
                                 "www.example.org", "m.example.org"})
        self.assertEqual(t[("api.example.com", TYPE_A)], {"A"})

    def test_regle_nue_est_domain(self):
        t = self.run_targets([app("A", ["example.org"])])
        self.assertIn(("www.example.org", TYPE_A), t)

    def test_regexp_keyword_exclus(self):
        t = self.run_targets([app("A", ["regexp:^a.*", "keyword:foo", "bar:x.com"])])
        self.assertEqual(t, {})

    def test_refusees_exclues(self):
        t = self.run_targets([app("A", ["domain:com", "domain:cloudfront.net",
                                        "full:ok.example.com"])])
        self.assertEqual({n for n, _ in t}, {"ok.example.com"})

    def test_partagees_exclues(self):
        t = self.run_targets([app("A", ["full:cloudfront.net", "domain:cloudfront.net",
                                        "full:ok.example.com"])])
        self.assertEqual({n for n, _ in t}, {"ok.example.com"})

    def test_tld_et_multilocataires_exclus(self):
        from lib import shared
        cfg, matcher = build([app("A", ["full:com", "domain:s3.amazonaws.com",
                                        "full:bucket.s3.amazonaws.com"])], ("www",))
        t = targets(cfg, matcher, shared.load(), logger=QUIET)
        self.assertEqual({n for n, _ in t}, {"bucket.s3.amazonaws.com"})

    def test_sous_domaine_dedie_cible(self):
        t = self.run_targets([app("A", ["full:d1.cloudfront.net", "domain:cloudfront.net"])])
        self.assertEqual({n for n, _ in t}, {"d1.cloudfront.net"})

    def test_desactivees_ou_non_proactives(self):
        t = self.run_targets([app("A", ["full:a.example.com"], enabled=False),
                              app("B", ["full:b.example.com"], proactive_=False),
                              app("C", ["full:c.example.com"])])
        self.assertEqual({n for n, _ in t}, {"c.example.com"})

    def test_qtypes(self):
        t = self.run_targets([app("A", ["full:a.example.com"], families="inet"),
                              app("B", ["full:b.example.com"], families="inet6"),
                              app("C", ["full:c.example.com"], families="both")])
        self.assertEqual({k for k in t if k[0] == "a.example.com"}, {("a.example.com", TYPE_A)})
        self.assertEqual({k for k in t if k[0] == "b.example.com"}, {("b.example.com", TYPE_AAAA)})
        self.assertEqual(len([k for k in t if k[0] == "c.example.com"]), 2)

    def test_union_deux_apps(self):
        t = self.run_targets([app("A", ["full:x.example.com"], families="inet"),
                              app("B", ["full:x.example.com"], families="inet6")])
        self.assertEqual(t, {("x.example.com", TYPE_A): {"A"},
                             ("x.example.com", TYPE_AAAA): {"B"}})
        t = self.run_targets([app("A", ["full:x.example.com"], families="inet"),
                              app("B", ["full:x.example.com"])])
        self.assertEqual(t[("x.example.com", TYPE_A)], {"A", "B"})
        self.assertEqual(t[("x.example.com", TYPE_AAAA)], {"B"})

    def test_plafond_par_application(self):
        logger, handler = capture("appalias.test.proactive.cap")
        rules = ["full:h%d.example.com" % i for i in range(proactive.MAX_PER_APP + 50)]
        t = self.run_targets([app("A", rules, families="inet")], logger=logger)
        self.assertEqual(len(t), proactive.MAX_PER_APP)
        self.assertEqual(len(handler.records), 1)

    def test_plafond_total(self):
        logger, handler = capture("appalias.test.proactive.total")
        apps = [app("T%d" % a, ["full:h%d-%d.example.com" % (a, i) for i in range(2000)],
                    families="inet") for a in range(11)]
        t = self.run_targets(apps, logger=logger)
        self.assertEqual(len(t), proactive.MAX_TOTAL)
        self.assertEqual(len(handler.records), 1)

    def test_pas_de_troncature_pas_davertissement(self):
        logger, handler = capture("appalias.test.proactive.none")
        self.run_targets([app("A", ["full:a.example.com"])], logger=logger)
        self.assertEqual(handler.records, [])


class InvalidNamesTests(unittest.TestCase):
    def run_targets(self, rules, logger=QUIET):
        apps = [app("A", rules)]
        cfg = SimpleNamespace(apps=apps, proactive_prefixes=["www"])
        matcher = Matcher({"A": rules}, logger=QUIET, platforms=PLATFORMS)
        return targets(cfg, matcher, PLATFORMS, logger=logger)

    def test_nom_trop_long_avec_prefixe(self):
        name = ".".join(["a" * 63] * 3 + ["b" * 60])
        self.assertEqual(len(name), 252)
        logger, handler = capture("appalias.test.proactive.long")
        logger.setLevel(logging.DEBUG)
        t = self.run_targets(["domain:" + name], logger)
        names = {n for n, _ in t}
        self.assertEqual(names, {name})              # « www.<nom> » dépasse 253
        self.assertEqual(len(handler.records), 1)
        self.assertEqual(handler.records[0].levelno, logging.DEBUG)

    def test_caracteres_invalides(self):
        logger, handler = capture("appalias.test.proactive.bad")
        logger.setLevel(logging.DEBUG)
        t = self.run_targets(["domain:foo bar.com", "full:é.com", "full:ok.example.com"], logger)
        self.assertEqual({n for n, _ in t}, {"ok.example.com"})
        self.assertEqual(len(handler.records), 1)    # message agrégé unique


class FixedRng:
    """rng dont uniform(a, b) renvoie a + (b - a) * frac."""

    def __init__(self, frac=0.0):
        self.frac = frac
        self.calls = []

    def uniform(self, a, b):
        self.calls.append((a, b))
        return a + (b - a) * self.frac


K1 = ("a.example.com", TYPE_A)
K2 = ("b.example.com", TYPE_A)
K3 = ("c.example.com", TYPE_AAAA)


class SchedulerTests(unittest.TestCase):
    def make(self, frac=1.0, now=1000.0, imin=300, imax=3600):
        self.now = now
        self.rng = FixedRng(frac)
        return Scheduler(lambda: self.now, imin, imax, self.rng)

    def start(self, s, keys=(K1,)):
        """Charge les cibles et les sort toutes (en vol) ; renvoie « maintenant »."""
        s.load({k: {"T"} for k in keys})
        far = self.now + 10 * s.interval_max
        self.assertEqual(sorted(s.due(far, 100)), sorted(keys))
        return far

    def test_premiere_passe_immediate(self):
        keys = [("h%d.example.com" % i, TYPE_A) for i in range(50)]
        s = self.make()
        s.load({k: set() for k in keys})
        self.assertEqual(s.due(self.now - 1, 100), [])
        self.assertEqual(s.next_due(), self.now)
        first = s.due(self.now, 10)                          # débit borné par l'appelant
        self.assertEqual(first, sorted(keys)[:10])           # ordre naturel du tas
        rest = s.due(self.now, 100)
        self.assertEqual(sorted(first + rest), sorted(keys))

    def test_etalement_initial_sur_demande(self):
        keys = [("h%d.example.com" % i, TYPE_A) for i in range(50)]
        s = self.make()
        s.load({k: set() for k in keys}, spread=True)
        self.assertEqual(s.due(self.now - 1, 100), [])
        half = s.due(self.now + 150, 100)
        self.assertTrue(0 < len(half) < len(keys))
        rest = s.due(self.now + 299.999, 100)
        self.assertEqual(sorted(half + rest), sorted(keys))
        # déterministe
        s2 = self.make()
        s2.load({k: set() for k in keys}, spread=True)
        self.assertEqual(sorted(s2.due(self.now + 150, 100)), sorted(half))

    def test_bornage_ttl_et_gigue(self):
        s = self.make(frac=1.0)
        now = self.start(s)
        s.done(K1, now, "ok", 10)  # sous interval_min
        self.assertEqual(s.due(now + 299, 10), [])
        self.assertEqual(s.due(now + 300, 10), [K1])
        s.done(K1, now, "ok", 999999)  # au-dessus d'interval_max
        self.assertEqual(s.due(now + 3599, 10), [])
        self.assertEqual(s.due(now + 3600, 10), [K1])
        s.done(K1, now, "ok", 1000)  # dans les bornes
        self.assertEqual(s.due(now + 999, 10), [])
        self.assertEqual(s.due(now + 1000, 10), [K1])
        self.assertTrue(all(c == (0.8, 1.0) for c in self.rng.calls))

    def test_gigue_bas_de_plage(self):
        s = self.make(frac=0.0)
        now = self.start(s)
        s.done(K1, now, "ok", 1000)
        self.assertEqual(s.due(now + 799, 10), [])
        self.assertEqual(s.due(now + 800, 10), [K1])

    def test_gigue_rng_reel(self):
        s = Scheduler(lambda: 0.0, 300, 3600, random.Random(42))
        s.load({K1: set()})
        self.assertEqual(s.due(10000, 10), [K1])
        for _ in range(20):
            s.done(K1, 0.0, "ok", 1000)
            nxt = s.next_due()
            self.assertTrue(800 <= nxt <= 1000)
            self.assertEqual(s.due(nxt, 10), [K1])

    def test_backoff_fail(self):
        s = self.make()
        now = self.start(s)
        for delay in (60, 120, 240, 480, 960, 1920, 3600, 3600):
            s.done(K1, now, "fail", None)
            self.assertEqual(s.due(now + delay - 1, 10), [])
            self.assertEqual(s.due(now + delay, 10), [K1])

    def test_backoff_nxdomain_nodata(self):
        s = self.make()
        now = self.start(s)
        for i, delay in enumerate((3600, 7200, 14400, 28800, 57600, 86400, 86400)):
            s.done(K1, now, "nxdomain" if i % 2 else "nodata", None)
            self.assertEqual(s.due(now + delay - 1, 10), [])
            self.assertEqual(s.due(now + delay, 10), [K1])

    def test_remise_a_zero_apres_ok(self):
        s = self.make(frac=1.0)
        now = self.start(s)
        for _ in range(3):
            s.done(K1, now, "fail", None)
            s.due(now + 100000, 10)
        s.done(K1, now, "ok", 300)
        s.due(now + 100000, 10)
        s.done(K1, now, "fail", None)
        self.assertEqual(s.due(now + 59, 10), [])
        self.assertEqual(s.due(now + 60, 10), [K1])

    def test_defer(self):
        s = self.make()
        now = self.start(s)
        s.defer(K1, now + 500)
        self.assertEqual(s.due(now + 499, 10), [])
        self.assertEqual(s.due(now + 500, 10), [K1])
        s.defer(("inconnu.example.com", TYPE_A), now)  # sans effet
        self.assertEqual(s.due(now + 10 ** 6, 10), [])

    def test_due_limit_et_en_vol(self):
        s = self.make()
        keys = [K1, K2, K3]
        s.load({k: set() for k in keys})
        far = self.now + 1000
        first = s.due(far, 2)
        self.assertEqual(len(first), 2)
        second = s.due(far, 2)
        self.assertEqual(len(second), 1)
        self.assertEqual(sorted(first + second), sorted(keys))
        self.assertEqual(s.due(far, 2), [])  # tout en vol

    def test_rechargement_conserve_echeances(self):
        s = self.make()
        now = self.start(s, (K1, K2))
        s.done(K1, now, "ok", 1000)
        s.done(K2, now, "ok", 3000)
        s.load({K1: set(), K2: set()})
        self.assertEqual(s.due(now + 999, 10), [])
        self.assertEqual(s.due(now + 1000, 10), [K1])
        self.assertEqual(s.due(now + 2999, 10), [])
        self.assertEqual(s.due(now + 3000, 10), [K2])

    def test_rechargement_nouvelle_cible_immediate(self):
        s = self.make()
        now = self.start(s, (K1,))
        s.done(K1, now, "ok", 3000)
        s.load({K1: set(), K3: set()})
        self.assertEqual(s.due(self.now - 1, 10), [])
        self.assertEqual(s.due(self.now, 10), [K3])         # nouvelle : tout de suite
        self.assertEqual(s.due(now + 2999, 10), [])          # K1 garde son échéance

    def test_rechargement_retire_et_done_tardif(self):
        s = self.make()
        now = self.start(s, (K1, K2))
        s.load({K2: set()})
        s.done(K1, now, "ok", 1000)  # K1 retirée : ignoré
        s.done(K1, now, "fail", None)
        s.defer(K1, now)
        self.assertEqual(s.due(now + 10 ** 6, 10), [])
        self.assertEqual(len(s), 1)


class SchedulerGuardTests(unittest.TestCase):
    def make(self):
        self.now = 1000.0
        return Scheduler(lambda: self.now, 300, 3600, FixedRng(1.0))

    def test_vol_jamais_acquitte_repart(self):
        s = self.make()
        s.load({K1: {"T"}})
        t0 = self.now + 300
        self.assertEqual(s.due(t0, 10), [K1])
        s.load({K1: {"T"}})                            # même nom : ne repousse pas
        self.assertEqual(s.due(t0 + proactive.INFLIGHT_GUARD - 1, 10), [])   # pas de double émission
        self.assertEqual(s.due(t0 + proactive.INFLIGHT_GUARD, 10), [])       # replanifiée (backoff d'échec)
        self.assertEqual(s.next_due(), t0 + proactive.INFLIGHT_GUARD + proactive.FAIL_BACKOFF_START)
        self.assertEqual(s.due(t0 + proactive.INFLIGHT_GUARD + proactive.FAIL_BACKOFF_START, 10), [K1])

    def test_next_due_voit_le_delai_de_garde(self):
        s = self.make()
        s.load({K1: {"T"}})
        t0 = self.now + 300
        s.due(t0, 10)
        self.assertEqual(s.next_due(), t0 + proactive.INFLIGHT_GUARD)

    def test_defer_libere_le_vol(self):
        s = self.make()
        s.load({K1: {"T"}})
        t0 = self.now + 300
        s.due(t0, 10)
        s.defer(K1, t0 + 500)
        self.assertEqual(s.due(t0 + 499, 10), [])
        self.assertEqual(s.due(t0 + 500, 10), [K1])


class SchedulerBackoffTests(unittest.TestCase):
    def test_nodata_apres_fails_repart_de_la_base(self):
        now = [1000.0]
        s = Scheduler(lambda: now[0], 300, 3600, FixedRng(1.0))
        s.load({K1: {"T"}})
        t = now[0] + 300
        delays = []
        for _ in range(8):                              # 60, 120, ... plafonné à 3600
            self.assertEqual(s.due(t, 10), [K1])
            s.done(K1, t, "fail")
            delays.append(s.next_due() - t)
            t = s.next_due()
        self.assertEqual(delays[-1], 3600)
        self.assertEqual(s.due(t, 10), [K1])
        s.done(K1, t, "nodata")
        self.assertEqual(s.next_due() - t, 3600)        # interval_max, pas 7200
        t = s.next_due()
        s.due(t, 10)
        s.done(K1, t, "nodata")
        self.assertEqual(s.next_due() - t, 7200)
        t = s.next_due()
        s.due(t, 10)
        s.done(K1, t, "fail")
        self.assertEqual(s.next_due() - t, 60)          # base des échecs
        t = s.next_due()
        s.due(t, 10)
        s.done(K1, t, "ok", 300)
        t = s.next_due()
        s.due(t, 10)
        s.done(K1, t, "nodata")
        self.assertEqual(s.next_due() - t, 3600)


LABELS = ["www", "api", "chat", "cdn"]


class DeepTargetsTests(unittest.TestCase):
    """0.10 : dictionnaire, indices, noms vus, jokers, plafonds."""

    def run_targets(self, apps, deep=True, **kw):
        cfg, matcher = build(apps, ("www",), deep=deep)
        kw.setdefault("labels", LABELS)
        return targets(cfg, matcher, PLATFORMS, logger=QUIET, **kw)

    @staticmethod
    def names(t):
        return {n for n, q in t if q != QTYPE_WILDCARD}

    def test_avec_dictionnaire(self):
        t = self.run_targets([app("A", ["domain:signal.org", "full:x.example.com"],
                                  families="inet")])
        self.assertEqual(self.names(t), {"signal.org", "www.signal.org", "api.signal.org",
                                         "chat.signal.org", "cdn.signal.org", "x.example.com"})
        self.assertEqual(t[("signal.org", QTYPE_WILDCARD)], {"A"})     # sonde joker
        self.assertNotIn(("x.example.com", QTYPE_WILDCARD), t)         # full: pas de dictionnaire

    def test_sans_dictionnaire(self):
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet")], deep=False)
        self.assertEqual(self.names(t), {"signal.org", "www.signal.org"})
        self.assertNotIn(("signal.org", QTYPE_WILDCARD), t)
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet")], labels=())
        self.assertEqual(set(t), {("signal.org", TYPE_A), ("www.signal.org", TYPE_A)})

    def test_deep_absent_vaut_vrai(self):
        apps = [app("A", ["domain:signal.org"], families="inet")]
        cfg = SimpleNamespace(apps=apps, proactive_prefixes=[])
        matcher = Matcher({"A": apps[0].domains}, logger=QUIET, platforms=PLATFORMS)
        t = targets(cfg, matcher, PLATFORMS, logger=QUIET, labels=["chat"])
        self.assertIn(("chat.signal.org", TYPE_A), t)

    def test_joker_retire_le_dictionnaire(self):
        apps = [app("A", ["domain:signal.org", "domain:example.org"], families="inet",
                    refs=["signal"])]
        t = self.run_targets(apps, wildcards={"signal.org"},
                             hints={"signal": ["storage.signal.org"]},
                             seen={"A": {"sfu.voip.signal.org": 5}})
        self.assertEqual({n for n in self.names(t) if n.endswith("signal.org")},
                         {"signal.org", "www.signal.org", "storage.signal.org"})
        self.assertNotIn(("sfu.voip.signal.org", TYPE_A), t)           # vu sous un joker
        self.assertNotIn(("signal.org", QTYPE_WILDCARD), t)
        self.assertIn(("chat.example.org", TYPE_A), t)

    def test_dictionnaire_attend_la_verification(self):
        apps = [app("A", ["domain:signal.org", "domain:example.org"], families="inet")]
        t = self.run_targets(apps, clear={"example.org"})
        self.assertIn(("chat.example.org", TYPE_A), t)
        self.assertNotIn(("chat.signal.org", TYPE_A), t)              # pas encore vérifié
        self.assertIn(("signal.org", QTYPE_WILDCARD), t)
        self.assertIn(("example.org", QTYPE_WILDCARD), t)             # revérifié plus tard

    def test_indices_seulement_dans_les_regles(self):
        hints = {"signal": ["chat.signal.org", "textsecure-service.whispersystems.org",
                            "evil.example.net"]}
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet",
                                  refs=["signal@-ads"])], deep=False, hints=hints)
        self.assertEqual(self.names(t), {"signal.org", "www.signal.org", "chat.signal.org"})
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet",
                                  refs=["autre"])], deep=False, hints=hints)
        self.assertNotIn(("chat.signal.org", TYPE_A), t)

    def test_noms_vus(self):
        seen = {"A": {"b.signal.org": 10, "a.signal.org": 20, "hors.example.net": 30},
                "B": {"c.signal.org": 5}}
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet6")],
                             deep=False, seen=seen)
        self.assertEqual(self.names(t), {"signal.org", "www.signal.org",
                                         "a.signal.org", "b.signal.org"})
        self.assertIn(("a.signal.org", TYPE_AAAA), t)
        self.assertNotIn(("a.signal.org", TYPE_A), t)

    def test_plafond_par_app_garde_la_base(self):
        labels = ["l%d" % i for i in range(100)]
        rules = ["domain:d%d.example.com" % i for i in range(30)]
        t = self.run_targets([app("A", rules, families="inet")], labels=labels,
                             report={})
        names = self.names(t)
        self.assertEqual(len(names), proactive.MAX_PER_APP)
        for i in range(30):                                            # base jamais tronquée
            self.assertIn("d%d.example.com" % i, names)
            self.assertIn("www.d%d.example.com" % i, names)

    def test_plafond_total_base_avant_dictionnaire(self):
        report = {}
        labels = ["l%d" % i for i in range(120)]
        apps = [app("T%d" % a, ["domain:a%d-%d.example.com" % (a, i) for i in range(16)],
                    families="inet") for a in range(12)]
        t = self.run_targets(apps, labels=labels, report=report)
        self.assertTrue(report["truncated"])
        self.assertEqual(len(self.names(t)), proactive.MAX_TOTAL)
        for a in range(12):                                            # base de chaque app servie
            self.assertIn(("www.a%d-15.example.com" % a, TYPE_A), t)


class DataFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        path = os.path.join(self.tmp.name, "f.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_labels_fichier_livre(self):
        labels = proactive.load_labels(logger=QUIET)
        self.assertGreaterEqual(len(labels), 80)
        self.assertLessEqual(len(labels), proactive.MAX_LABELS)
        for label in ("www", "api", "chat", "cdn", "storage", "voip", "sfu", "updates"):
            self.assertIn(label, labels)
        self.assertEqual(len(labels), len(set(labels)))

    def test_labels_invalides_et_plafond(self):
        logger, handler = capture("appalias.test.proactive.labels")
        path = self.write("# commentaire\nWWW\napi  # fin\nbad.label\n-x\nwww\n"
                          + "\n".join("l%d" % i for i in range(200)))
        labels = proactive.load_labels(path, logger=logger)
        self.assertEqual(labels[:2], ["www", "api"])
        self.assertEqual(len(labels), proactive.MAX_LABELS)
        self.assertTrue(handler.records)

    def test_labels_fichier_absent(self):
        logger, handler = capture("appalias.test.proactive.nolabels")
        self.assertEqual(proactive.load_labels(os.path.join(self.tmp.name, "absent.txt"),
                                               logger=logger), [])
        self.assertEqual(len(handler.records), 1)
        self.assertEqual(handler.records[0].levelno, logging.WARNING)

    def test_indices(self):
        path = self.write("# c\nsignal: chat.signal.org CDN.signal.org. chat.signal.org\n"
                          "vide:\nmauvais ligne\nx: nom_invalide! ok.example.com\n")
        self.assertEqual(proactive.load_hints(path, logger=QUIET),
                         {"signal": ["chat.signal.org", "cdn.signal.org"],
                          "x": ["ok.example.com"]})
        self.assertEqual(proactive.load_hints(os.path.join(self.tmp.name, "absent"),
                                              logger=QUIET), {})

    def test_indices_fichier_livre(self):
        hints = proactive.load_hints(logger=QUIET)
        self.assertIn("sfu.voip.signal.org", hints["signal"])
        self.assertEqual(set(hints), {"signal", "whatsapp", "telegram", "discord", "twitch",
                                      "spotify", "netflix"})

    def test_aides(self):
        t = {("a.example.com", TYPE_A): {"A", "B"}, ("a.example.com", TYPE_AAAA): {"A"},
             ("b.example.com", TYPE_A): {"B"}, ("example.com", QTYPE_WILDCARD): {"A"}}
        self.assertEqual(proactive.names_by_table(t), {"A": 1, "B": 2})
        self.assertEqual(proactive.count_names(t), 2)
        self.assertEqual(proactive.ref_list("Netflix@-ads"), "netflix")


class RelectureTests(unittest.TestCase):
    """Corrections après relecture : UTF-8 invalide, noms vus lents, plafond de re-sonde."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_utf8_invalide(self):
        path = os.path.join(self.tmp.name, "bad.txt")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfe")
        logger, handler = capture("appalias.test.proactive.utf8")
        self.assertEqual(proactive.load_labels(path, logger=logger), [])
        self.assertEqual(proactive.load_hints(path, logger=logger), {})
        self.assertEqual(len(handler.records), 2)
        self.assertTrue(all(r.levelno == logging.WARNING for r in handler.records))

    def run_targets(self, apps, **kw):
        cfg, matcher = build(apps, ("www",), deep=True)
        return targets(cfg, matcher, PLATFORMS, logger=QUIET, **kw)

    def test_noms_vus_lents(self):
        report = {}
        seen = {"A": {"vu.signal.org": 5, "chat.signal.org": 6, "www.signal.org": 7}}
        t = self.run_targets([app("A", ["domain:signal.org"], families="inet")],
                             labels=["chat"], clear={"signal.org"}, seen=seen, report=report)
        self.assertIn(("vu.signal.org", TYPE_A), t)
        self.assertEqual(report["slow"], {("vu.signal.org", TYPE_A)})   # chat : dictionnaire

    def test_lent_seulement_si_vu_partout(self):
        report = {}
        seen = {"A": {"x.example.com": 5}}
        t = self.run_targets([app("A", ["domain:example.com"], families="inet"),
                              app("B", ["full:x.example.com"], families="inet")],
                             seen=seen, report=report)
        self.assertEqual(t[("x.example.com", TYPE_A)], {"A", "B"})
        self.assertEqual(report["slow"], set())

    def test_plafond_de_resonde(self):
        report = {}
        seen = {"A": {"n%d.example.com" % i: i for i in range(400)}}
        t = self.run_targets([app("A", ["domain:example.com"], families="inet")],
                             seen=seen, report=report)
        self.assertEqual(len(report["slow"]), proactive.MAX_SEEN_PROBED)
        self.assertIn(("n399.example.com", TYPE_A), t)                   # les plus récents
        self.assertNotIn(("n0.example.com", TYPE_A), t)

    def test_domaine_inexistant(self):
        t = self.run_targets([app("A", ["domain:absent.example", "domain:ok.example"],
                                  families="inet")],
                             labels=["chat"], clear={"absent.example", "ok.example"},
                             missing={"absent.example"})
        self.assertIn(("absent.example", TYPE_A), t)                     # revérifié (backoff)
        self.assertNotIn(("chat.absent.example", TYPE_A), t)
        self.assertNotIn(("absent.example", QTYPE_WILDCARD), t)
        self.assertIn(("chat.ok.example", TYPE_A), t)

    def test_under_wildcard(self):
        self.assertTrue(proactive.under_wildcard("a.b.example.com", {"example.com"}))
        self.assertFalse(proactive.under_wildcard("example.com", {"example.com"}))
        self.assertFalse(proactive.under_wildcard("a.example.org", {"example.com"}))
        self.assertFalse(proactive.under_wildcard("a.example.com", set()))


class SchedulerSlowTests(unittest.TestCase):
    def test_lente_apres_les_cibles_de_base(self):
        now = [1000.0]
        s = Scheduler(lambda: now[0], 300, 3600, FixedRng(1.0))
        s.load({K1: set(), K2: set()}, slow={K2})
        self.assertEqual(s.due(1000.0, 10), [K1])
        s.done(K1, 1000.0, "ok", 3600)
        self.assertEqual(s.due(1299.0, 10), [])
        self.assertEqual(s.due(1300.0, 10), [K2])
        s.done(K2, 1300.0, "ok", 60)                       # TTL court : interval_max quand même
        self.assertEqual(s._state[K2]["due"], 1300.0 + 3600)

    def test_classe_mise_a_jour_au_rechargement(self):
        now = [1000.0]
        s = Scheduler(lambda: now[0], 300, 3600, FixedRng(1.0))
        s.load({K1: set()}, slow={K1})
        s.load({K1: set()})
        s.due(10 ** 6, 10)
        s.done(K1, 2000.0, "ok", 600)
        self.assertEqual(s.next_due(), 2600.0)

    def test_etalement_garde_les_sondes_joker_en_tete(self):
        now = [1000.0]
        s = Scheduler(lambda: now[0], 300, 3600, FixedRng(1.0))
        wild = ("example.com", QTYPE_WILDCARD)
        s.load({K1: set(), K2: set(), wild: set()}, spread=True)
        self.assertEqual(s.due(1000.0, 1), [wild])


if __name__ == "__main__":
    unittest.main()
