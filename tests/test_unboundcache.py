import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import appalias_testutil  # noqa: F401

from lib import unboundcache
from lib.unboundcache import UnboundCacheError, collect, parse

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "unbound", "dump_cache.txt")


def lire():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


class ParseTests(unittest.TestCase):
    def test_fixture(self):
        self.assertEqual(sorted(parse(lire())), sorted([
            ("www.example.com", "93.184.216.34", 3600),
            ("www.example.com", "2606:2800:220:1:248:1893:25c8:1946", 3600),
            ("cdn.example.net", "192.0.2.10", 300),
            ("api.example.org", "198.51.100.7", 120),
            ("api.example.org", "198.51.100.8", 120),
            ("v6.example.com", "2001:db8::1", 900),
        ]))

    def test_dedoublonnage_ttl_max(self):
        txt = ("START_RRSET_CACHE\n"
               "a.test.\t10\tIN\tA\t192.0.2.1\n"
               "A.TEST\t99\tIN\tA\t192.0.2.1\n"
               "a.test.\t50\tIN\tA\t192.0.2.1\n"
               "END_RRSET_CACHE\n")
        self.assertEqual(parse(txt), [("a.test", "192.0.2.1", 99)])

    def test_plafond_ttl(self):
        txt = "START_RRSET_CACHE\na.test.\t99999999\tIN\tA\t192.0.2.1\nEND_RRSET_CACHE\n"
        self.assertEqual(parse(txt)[0][2], 7 * 24 * 3600)

    def test_max_records(self):
        txt = "START_RRSET_CACHE\n" + "".join(
            "h%d.test.\t10\tIN\tA\t192.0.2.%d\n" % (i, i) for i in range(1, 6)
        ) + "END_RRSET_CACHE\n"
        with mock.patch.object(unboundcache, "MAX_RECORDS", 3):
            self.assertEqual(len(parse(txt)), 3)

    def test_vide(self):
        self.assertEqual(parse(""), [])
        self.assertEqual(parse(None), [])
        self.assertEqual(parse("\x00\xff garbage"), [])


class CollectTests(unittest.TestCase):
    def test_succes_et_argv(self):
        vus = []

        def runner(argv, timeout):
            vus.append((argv, timeout))
            return SimpleNamespace(returncode=0, stdout=lire().encode("utf-8"), stderr=b"")

        res = collect(runner=runner)
        self.assertEqual(len(res), 6)
        self.assertEqual(vus, [(["/usr/local/sbin/unbound-control", "-c",
                                 "/var/unbound/unbound.conf", "dump_cache"], 20.0)])

    def test_code_non_nul(self):
        def runner(argv, timeout):
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

        with self.assertRaises(UnboundCacheError):
            collect(runner=runner)

    def test_sortie_trop_grande(self):
        def runner(argv, timeout):
            return SimpleNamespace(returncode=0, stdout=b"x" * 50, stderr=b"")

        with mock.patch.object(unboundcache, "MAX_OUTPUT", 10):
            with self.assertRaises(UnboundCacheError):
                collect(runner=runner)

    def test_oserror_et_timeout(self):
        def r1(argv, timeout):
            raise OSError("absent")

        def r2(argv, timeout):
            raise subprocess.TimeoutExpired(argv, timeout)

        for r in (r1, r2):
            with self.assertRaises(UnboundCacheError):
                collect(runner=r)


def python_collect(code, timeout=20.0):
    """collect() sur un faux unbound-control : « python -c <code> dump_cache »."""
    return collect(control=sys.executable, conf=code, timeout=timeout)


class RunTests(unittest.TestCase):
    """Exécution réelle bornée (I2) : lecture par blocs, plafond, délai global."""

    def test_sortie_octets_invalides(self):
        code = ("import sys; sys.stdout.buffer.write(b'START_RRSET_CACHE\\n"
                "a.test.\\t60\\tIN\\tA\\t192.0.2.1\\n\\xff\\xfe\\nEND_RRSET_CACHE\\n')")
        self.assertEqual(python_collect(code), [("a.test", "192.0.2.1", 60)])

    def test_plafond_tue_le_processus(self):
        code = "import sys\nwhile True: sys.stdout.write('x' * 65536)"
        with mock.patch.object(unboundcache, "MAX_OUTPUT", 200000):
            with self.assertRaises(UnboundCacheError) as cm:
                python_collect(code)
        self.assertIn("volumineuse", str(cm.exception))

    def test_delai_global(self):
        code = ("import time, sys\nwhile True:\n"
                "    sys.stdout.write('x'); sys.stdout.flush(); time.sleep(0.05)")
        with self.assertRaises(UnboundCacheError) as cm:
            python_collect(code, timeout=0.5)
        self.assertIn("délai", str(cm.exception))

    def test_code_retour_et_stderr(self):
        code = "import sys; sys.stderr.buffer.write(b'erreur \\xe9'); sys.exit(3)"
        with self.assertRaises(UnboundCacheError) as cm:
            python_collect(code)
        self.assertIn("code 3", str(cm.exception))
        self.assertIn("erreur", str(cm.exception))

    def test_runner_texte_accepte(self):
        def runner(argv, timeout):
            return SimpleNamespace(returncode=0, stdout=lire(), stderr="")

        self.assertEqual(len(collect(runner=runner)), 6)


if __name__ == "__main__":
    unittest.main()
