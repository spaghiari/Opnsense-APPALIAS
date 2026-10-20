import ctypes
import errno
import logging
import unittest

import appalias_testutil  # noqa: F401  (prépare sys.path)

from lib import pfioctl as pio
from lib.pftable import PfError, PfTable, make_backend, normalize_entry


class FakePfDev:
    """Faux noyau pf en mémoire : table -> liste d'entrées canoniques."""

    def __init__(self):
        self.tables = {}
        self.calls = []
        self.fail_with = None
        self.show_empty = False     # noyau incohérent : GETADDRS ne rend rien

    def __call__(self, fd, request, io):
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        name = bytes(io.pfrio_table.pfrt_name).split(b"\0")[0].decode()
        if request == pio.DIOCRGETTABLES:
            if io.pfrio_esize != ctypes.sizeof(pio.pfr_table):
                raise OSError(errno.EINVAL, "Invalid argument")
            io.pfrio_size = len(self.tables)
            return 0
        if request == pio.DIOCRADDTABLES:
            if io.pfrio_esize != ctypes.sizeof(pio.pfr_table) or io.pfrio_size != 1:
                raise OSError(errno.EINVAL, "Invalid argument")
            tbl = pio.pfr_table.from_address(io.pfrio_buffer)
            if tbl.pfrt_flags != pio.PFR_TFLAG_PERSIST:
                raise OSError(errno.EINVAL, "Invalid argument")
            self.tables.setdefault(tbl.pfrt_name.decode(), [])
            io.pfrio_nadd = 1
            return 0
        if request == pio.DIOCRDELTABLES:
            if io.pfrio_esize != ctypes.sizeof(pio.pfr_table) or io.pfrio_size != 1:
                raise OSError(errno.EINVAL, "Invalid argument")
            tbl = pio.pfr_table.from_address(io.pfrio_buffer)
            io.pfrio_ndel = 1 if self.tables.pop(tbl.pfrt_name.decode(), None) is not None else 0
            return 0
        if io.pfrio_esize != ctypes.sizeof(pio.pfr_addr):
            raise OSError(errno.EINVAL, "Invalid argument")
        if name not in self.tables:
            raise OSError(errno.ESRCH, "No such process")
        cur = self.tables[name]
        size = io.pfrio_size
        addrs = []
        if request != pio.DIOCRGETADDRS and size and io.pfrio_buffer:
            addrs = list((pio.pfr_addr * size).from_address(io.pfrio_buffer))
        if request == pio.DIOCRADDADDRS:
            n = 0
            for e in [pio._from_addr(a) for a in addrs]:
                if e not in cur:
                    cur.append(e)
                    n += 1
            io.pfrio_nadd = n
        elif request == pio.DIOCRDELADDRS:
            n = 0
            for e in [pio._from_addr(a) for a in addrs]:
                if e in cur:
                    cur.remove(e)
                    n += 1
            io.pfrio_ndel = n
        elif request == pio.DIOCRCLRADDRS:
            io.pfrio_ndel = len(cur)
            cur.clear()
        elif request == pio.DIOCRSETADDRS:
            cur[:] = [pio._from_addr(a) for a in addrs]
        elif request == pio.DIOCRGETADDRS:
            if self.show_empty:
                io.pfrio_size = 0
                return 0
            if size >= len(cur) and size and io.pfrio_buffer:
                arr = (pio.pfr_addr * size).from_address(io.pfrio_buffer)
                for i, e in enumerate(cur):
                    arr[i] = pio._to_addr(e)
            io.pfrio_size = len(cur)
        else:
            raise OSError(errno.ENOTTY, "Inappropriate ioctl")
        return 0


def make():
    dev = FakePfDev()
    return pio.PfIoctl(ioctl=dev, opener=lambda d: -1), dev


class StructTests(unittest.TestCase):
    def test_tailles(self):
        self.assertEqual(ctypes.sizeof(pio.pfr_table), 1064)
        self.assertEqual(ctypes.sizeof(pio.pfr_addr), 20)
        self.assertEqual(ctypes.sizeof(pio.pfioc_table), 1104)

    def test_requetes(self):
        self.assertEqual(pio.DIOCRADDTABLES, 0xC450443D)
        self.assertEqual(pio.DIOCRGETTABLES, 0xC450443F)
        self.assertEqual(pio.DIOCRCLRADDRS, 0xC4504442)
        self.assertEqual(pio.DIOCRADDADDRS, 0xC4504443)
        self.assertEqual(pio.DIOCRDELADDRS, 0xC4504444)
        self.assertEqual(pio.DIOCRSETADDRS, 0xC4504445)
        self.assertEqual(pio.DIOCRGETADDRS, 0xC4504446)
        self.assertEqual(pio.DIOCRDELTABLES, 0xC450443E)


class BackendTests(unittest.TestCase):
    def test_add_v4_v6_reseau(self):
        pf, dev = make()
        dev.tables["t1"] = []
        n = pf.add("t1", ["192.0.2.1", "2001:db8::1", "198.51.100.0/24"])
        self.assertEqual(n, 3)
        self.assertEqual(dev.tables["t1"],
                         ["192.0.2.1", "2001:db8::1", "198.51.100.0/24"])

    def test_conversion_pfr_addr(self):
        a = pio._to_addr("192.0.2.1")
        self.assertEqual((a.pfra_af, a.pfra_net), (2, 32))
        self.assertEqual(bytes(a.pfra_u[:4]), bytes([192, 0, 2, 1]))
        a = pio._to_addr("2001:db8::/32")
        self.assertEqual((a.pfra_af, a.pfra_net), (28, 32))
        self.assertEqual(bytes(a.pfra_u[:4]), bytes([0x20, 0x01, 0x0d, 0xb8]))
        a = pio._to_addr("198.51.100.77/24")
        self.assertEqual(a.pfra_net, 24)
        self.assertEqual(bytes(a.pfra_u[:4]), bytes([198, 51, 100, 0]))

    def test_creation_auto_puis_retry(self):
        pf, dev = make()
        pf.add("neuve", ["192.0.2.9"])
        self.assertEqual(dev.tables["neuve"], ["192.0.2.9"])
        self.assertEqual(dev.calls, [pio.DIOCRADDADDRS, pio.DIOCRADDTABLES,
                                     pio.DIOCRADDADDRS])

    def test_delete_flush_replace_show(self):
        pf, dev = make()
        pf.add("t", ["192.0.2.1", "192.0.2.2", "2001:db8::/32"])
        self.assertEqual(pf.delete("t", ["192.0.2.1"]), 1)
        self.assertEqual(pf.show("t"), ["192.0.2.2", "2001:db8::/32"])
        pf.replace("t", ["203.0.113.5"])
        self.assertEqual(pf.show("t"), ["203.0.113.5"])
        pf.replace("t", [])
        self.assertEqual(pf.show("t"), [])
        pf.add("t", ["192.0.2.3"])
        pf.flush("t")
        self.assertEqual(dev.tables["t"], [])

    def test_show_deux_temps(self):
        pf, dev = make()
        pf.add("t", ["192.0.2.%d" % i for i in range(1, 101)])
        dev.calls.clear()
        self.assertEqual(len(pf.show("t")), 100)
        self.assertEqual(dev.calls, [pio.DIOCRGETADDRS, pio.DIOCRGETADDRS])

    def test_liste_vide_aucun_appel(self):
        pf, dev = make()
        self.assertEqual(pf.add("t", []), 0)
        self.assertEqual(pf.delete("t", []), 0)
        self.assertEqual(dev.calls, [])

    def test_nom_invalide(self):
        pf, dev = make()
        for op in (lambda: pf.add("a b", ["192.0.2.1"]),
                   lambda: pf.flush("x;y"),
                   lambda: pf.show("")):
            with self.assertRaises(PfError):
                op()
        self.assertEqual(dev.calls, [])

    def test_oserror_devient_pferror(self):
        pf, dev = make()
        dev.fail_with = OSError(errno.EPERM, "Operation not permitted")
        with self.assertRaises(PfError) as cm:
            pf.flush("t")
        msg = str(cm.exception)
        self.assertIn("CLRADDRS", msg)
        self.assertIn(" t ", msg)
        self.assertIn("[Errno %d] Operation not permitted" % errno.EPERM, msg)

    def test_probe(self):
        pf, dev = make()
        dev.tables.update({"a": [], "b": []})
        self.assertEqual(pf.probe(), 2)

    def test_probe_echec(self):
        pf, dev = make()
        dev.fail_with = OSError(errno.ENOENT, "No such file")
        with self.assertRaises(PfError):
            pf.probe()

    def test_ouverture_impossible(self):
        def boom(dev):
            raise FileNotFoundError(errno.ENOENT, "No such file")
        with self.assertRaises(PfError):
            pio.PfIoctl(ioctl=lambda *a: 0, opener=boom)

    def test_noms(self):
        self.assertEqual(pio.PfIoctl.name, "ioctl")
        self.assertEqual(PfTable.name, "pfctl")
        self.assertEqual(normalize_entry("192.0.2.1"), "192.0.2.1")


def _boom():
    raise PfError("pas de /dev/pf")


class SelftestTests(unittest.TestCase):
    def test_selftest_ok(self):
        pf, dev = make()
        pf.selftest()
        self.assertNotIn(pio.PROBE_TABLE, dev.tables)          # table supprimée
        self.assertIn(pio.DIOCRDELTABLES, dev.calls)

    def test_selftest_incoherent(self):
        pf, dev = make()
        dev.show_empty = True
        with self.assertRaises(PfError):
            pf.selftest()
        self.assertNotIn(pio.PROBE_TABLE, dev.tables)

    def test_auto_selftest_ok(self):
        pf, dev = make()
        self.assertIs(make_backend("auto", ioctl_factory=lambda: pf), pf)
        self.assertIsNone(pf.fallback_reason)
        self.assertEqual(dev.calls[0], pio.DIOCRGETTABLES)

    def test_auto_selftest_ko_repli(self):
        pf, dev = make()
        dev.show_empty = True
        with self.assertLogs("appalias", level="WARNING"):
            b = make_backend("auto", ioctl_factory=lambda: pf)
        self.assertIsInstance(b, PfTable)
        self.assertIn("selftest", b.fallback_reason)

    def test_ioctl_selftest_ko(self):
        pf, dev = make()
        dev.show_empty = True
        with self.assertRaises(PfError):
            make_backend("ioctl", ioctl_factory=lambda: pf)

    def test_show_cap_borne(self):
        pf, dev = make()
        dev.tables["t"] = []
        seen = {}

        def fake_getaddrs(fd, request, io):
            if request == pio.DIOCRGETADDRS:
                if io.pfrio_size:
                    seen["cap"] = io.pfrio_size
                io.pfrio_size = 200000 if not io.pfrio_size else 0
            return 0

        pf._ioctl = fake_getaddrs
        pf.show("t")
        self.assertEqual(seen["cap"], pio.PF_IOCTL_MAXCOUNT)


class MakeBackendTests(unittest.TestCase):
    def test_pfctl(self):
        self.assertIsInstance(make_backend("pfctl"), PfTable)

    def test_ioctl_echec(self):
        with self.assertRaises(PfError):
            make_backend("ioctl", ioctl_factory=_boom)

    def test_auto_repli(self):
        with self.assertLogs("appalias", level="WARNING") as cm:
            b = make_backend("auto", ioctl_factory=_boom)
        self.assertIsInstance(b, PfTable)
        self.assertIn("repli sur pfctl", cm.output[0])

    def test_auto_log_injecte(self):
        log = logging.getLogger("appalias.test_pfioctl")
        with self.assertLogs(log, level="WARNING"):
            b = make_backend("auto", log=log, ioctl_factory=_boom)
        self.assertIsInstance(b, PfTable)

    def test_auto_succes(self):
        sentinel = object()
        self.assertIs(make_backend("auto", ioctl_factory=lambda: sentinel), sentinel)

    def test_mode_inconnu(self):
        with self.assertRaises(ValueError):
            make_backend("zzz")


if __name__ == "__main__":
    unittest.main()
