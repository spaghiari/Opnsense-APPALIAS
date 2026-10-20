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

"""Backend des tables pf par ioctl direct sur /dev/pf (sans fork de pfctl).

Même interface que pftable.PfTable (add, delete, flush, replace, show).

Définitions FreeBSD amd64 (sys/net/pfvar.h, sys/sys/ioccom.h) :
- struct pfr_table   : anchor[1024], name[32], flags u32, fback u8 (1064 octets) ;
- struct pfr_addr    : u[16], af u8, net u8, not u8, fback u8 (20 octets) ;
- struct pfioc_table : pfr_table, buffer (pointeur), esize, size, size2, nadd,
  ndel, nchange, flags (int), ticket u32 (1104 octets) ;
- requêtes DIOCR* = _IOWR('D', n, struct pfioc_table) :
  ADDTABLES=61, DELTABLES=62, GETTABLES=63, CLRADDRS=66, ADDADDRS=67,
  DELADDRS=68, SETADDRS=69, GETADDRS=70.
Les valeurs AF_INET (2) et AF_INET6 (28) sont celles de FreeBSD, pas celles
de la bibliothèque socket de l'hôte de développement.

Le tampon passé à l'ioctl est la structure pfioc_table ctypes elle-même
(objet inscriptible, modifié sur place par le noyau). Si la taille d'une
structure diffère de l'attendu (autre plateforme), la construction de
PfIoctl lève PfError, ce qui déclenche le repli sur pfctl en mode auto.
"""

import ctypes
import errno
import ipaddress
import os
import threading

from .pftable import PfError, check_table, normalize_entry

PF_DEV = "/dev/pf"
MAXPATHLEN = 1024
PF_TABLE_NAME_SIZE = 32
AF_INET = 2
AF_INET6 = 28
PFR_TFLAG_PERSIST = 0x00000001
PF_IOCTL_MAXCOUNT = 65535        # plafond d'éléments par ioctl (pf_ioctl_maxcount)
PROBE_TABLE = "appalias_selftest_0x7e"   # table temporaire de selftest(), nom improbable
PROBE_ENTRIES = ("192.0.2.1", "2001:db8::1")

IOCPARM_MASK = 0x1FFF
IOC_OUT = 0x40000000
IOC_IN = 0x80000000
IOC_INOUT = IOC_IN | IOC_OUT


class pfr_table(ctypes.Structure):
    _fields_ = [("pfrt_anchor", ctypes.c_char * MAXPATHLEN),
                ("pfrt_name", ctypes.c_char * PF_TABLE_NAME_SIZE),
                ("pfrt_flags", ctypes.c_uint32),
                ("pfrt_fback", ctypes.c_uint8)]


class pfr_addr(ctypes.Structure):
    _fields_ = [("pfra_u", ctypes.c_uint8 * 16),
                ("pfra_af", ctypes.c_uint8),
                ("pfra_net", ctypes.c_uint8),
                ("pfra_not", ctypes.c_uint8),
                ("pfra_fback", ctypes.c_uint8)]


class pfioc_table(ctypes.Structure):
    _fields_ = [("pfrio_table", pfr_table),
                ("pfrio_buffer", ctypes.c_void_p),
                ("pfrio_esize", ctypes.c_int),
                ("pfrio_size", ctypes.c_int),
                ("pfrio_size2", ctypes.c_int),
                ("pfrio_nadd", ctypes.c_int),
                ("pfrio_ndel", ctypes.c_int),
                ("pfrio_nchange", ctypes.c_int),
                ("pfrio_flags", ctypes.c_int),
                ("pfrio_ticket", ctypes.c_uint32)]


def _IOWR(group, num, size):
    return IOC_INOUT | ((size & IOCPARM_MASK) << 16) | (ord(group) << 8) | num


DIOCRADDTABLES = _IOWR('D', 61, ctypes.sizeof(pfioc_table))
DIOCRDELTABLES = _IOWR('D', 62, ctypes.sizeof(pfioc_table))
DIOCRGETTABLES = _IOWR('D', 63, ctypes.sizeof(pfioc_table))
DIOCRCLRADDRS = _IOWR('D', 66, ctypes.sizeof(pfioc_table))
DIOCRADDADDRS = _IOWR('D', 67, ctypes.sizeof(pfioc_table))
DIOCRDELADDRS = _IOWR('D', 68, ctypes.sizeof(pfioc_table))
DIOCRSETADDRS = _IOWR('D', 69, ctypes.sizeof(pfioc_table))
DIOCRGETADDRS = _IOWR('D', 70, ctypes.sizeof(pfioc_table))

_EXPECTED_SIZES = ((pfr_table, 1064), (pfr_addr, 20), (pfioc_table, 1104))


def _check_sizes():
    """Tailles attendues sur FreeBSD amd64 ; PfError sinon (jamais d'assert)."""
    for cls, expected in _EXPECTED_SIZES:
        got = ctypes.sizeof(cls)
        if got != expected:
            raise PfError("structure %s : taille %d au lieu de %d (plateforme "
                          "non supportée)" % (cls.__name__, got, expected))


def _default_ioctl(fd, request, buf):
    import fcntl  # absent sous Windows : import paresseux
    return fcntl.ioctl(fd, request, buf, True)


def _default_opener(dev):
    return os.open(dev, os.O_RDWR)


def _to_addr(entry):
    """Entrée -> pfr_addr (octets de l'adresse réseau, famille, préfixe)."""
    net = ipaddress.ip_network(normalize_entry(entry), strict=False)
    addr = pfr_addr()
    for i, b in enumerate(net.network_address.packed):
        addr.pfra_u[i] = b
    addr.pfra_af = AF_INET if net.version == 4 else AF_INET6
    addr.pfra_net = net.prefixlen
    return addr


def _from_addr(addr):
    """pfr_addr -> forme canonique (192.0.2.1, 2001:db8::/32)."""
    if addr.pfra_af == AF_INET:
        ip = ipaddress.IPv4Address(bytes(addr.pfra_u[:4]))
        maxlen = 32
    else:
        ip = ipaddress.IPv6Address(bytes(addr.pfra_u[:16]))
        maxlen = 128
    if addr.pfra_net >= maxlen:
        return str(ip)
    return "%s/%d" % (ip, addr.pfra_net)


class PfIoctl:
    name = "ioctl"

    def __init__(self, dev=PF_DEV, ioctl=None, opener=None):
        self._fd = None
        _check_sizes()
        self._lock = threading.Lock()
        self._ioctl = ioctl if ioctl is not None else _default_ioctl
        opener = opener if opener is not None else _default_opener
        try:
            self._fd = opener(dev)
        except OSError as exc:
            raise PfError("ouverture de %s impossible : %s" % (dev, exc)) from exc

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _call(self, opname, table, request, io):
        """Un ioctl sous verrou ; OSError -> PfError (attribut errno conservé)."""
        try:
            with self._lock:
                self._ioctl(self._fd, request, io)
        except OSError as exc:
            err = PfError("pf ioctl %s %s : [Errno %s] %s"
                          % (opname, table, exc.errno, exc.strerror or exc))
            err.errno = exc.errno
            raise err from exc

    def probe(self):
        """Vérifie /dev/pf (DIOCRGETTABLES) ; renvoie le nombre de tables."""
        io = pfioc_table()
        io.pfrio_esize = ctypes.sizeof(pfr_table)
        io.pfrio_size = 0
        self._call("GETTABLES", "-", DIOCRGETTABLES, io)
        return io.pfrio_size

    def selftest(self):
        """Aller-retour complet sur PROBE_TABLE : add, show, delete, suppression.

        Lève PfError à la moindre erreur ou incohérence (structures mal
        interprétées par le noyau) ; la table de test est supprimée dans tous
        les cas (au mieux).
        """
        try:
            self.add(PROBE_TABLE, list(PROBE_ENTRIES))
            seen = set(self.show(PROBE_TABLE))
            missing = [e for e in PROBE_ENTRIES if e not in seen]
            if missing:
                raise PfError("selftest ioctl : %s absent(s) après ajout (table : %s)"
                              % (", ".join(missing), ", ".join(sorted(seen)) or "vide"))
            self.delete(PROBE_TABLE, list(PROBE_ENTRIES))
            left = [e for e in self.show(PROBE_TABLE) if e in PROBE_ENTRIES]
            if left:
                raise PfError("selftest ioctl : %s encore présent(s) après retrait"
                              % ", ".join(left))
        finally:
            try:
                self._delete_table(PROBE_TABLE)
            except PfError:
                pass

    def _delete_table(self, table):
        tbl = pfr_table()
        tbl.pfrt_name = table.encode("ascii")
        io = pfioc_table()
        io.pfrio_esize = ctypes.sizeof(pfr_table)
        io.pfrio_size = 1
        io.pfrio_buffer = ctypes.addressof(tbl)
        self._call("DELTABLES", table, DIOCRDELTABLES, io)

    def _create_table(self, table):
        tbl = pfr_table()
        tbl.pfrt_name = table.encode("ascii")
        tbl.pfrt_flags = PFR_TFLAG_PERSIST
        io = pfioc_table()
        io.pfrio_esize = ctypes.sizeof(pfr_table)
        io.pfrio_size = 1
        io.pfrio_buffer = ctypes.addressof(tbl)
        self._call("ADDTABLES", table, DIOCRADDTABLES, io)

    def _addr_op(self, opname, request, table, items):
        """Opération sur adresses ; crée la table (ESRCH) puis réessaie une fois."""
        addrs = [_to_addr(e) for e in items]
        arr = (pfr_addr * max(len(addrs), 1))(*addrs)

        def attempt():
            io = pfioc_table()
            io.pfrio_table.pfrt_name = table.encode("ascii")
            io.pfrio_esize = ctypes.sizeof(pfr_addr)
            io.pfrio_size = len(addrs)
            io.pfrio_buffer = ctypes.addressof(arr) if addrs else None
            self._call(opname, table, request, io)
            return io

        try:
            return attempt()
        except PfError as exc:
            if getattr(exc, "errno", None) != errno.ESRCH:
                raise
            self._create_table(table)
            return attempt()

    def add(self, table, entries):
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        if not items:
            return 0
        return self._addr_op("ADDADDRS", DIOCRADDADDRS, table, items).pfrio_nadd

    def delete(self, table, entries):
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        if not items:
            return 0
        return self._addr_op("DELADDRS", DIOCRDELADDRS, table, items).pfrio_ndel

    def flush(self, table):
        check_table(table)
        self._addr_op("CLRADDRS", DIOCRCLRADDRS, table, [])

    def replace(self, table, entries):
        check_table(table)
        items = [normalize_entry(e) for e in entries]
        self._addr_op("SETADDRS", DIOCRSETADDRS, table, items)

    def show(self, table):
        """Contenu de la table, dans l'ordre du noyau (non trié)."""
        check_table(table)
        io = pfioc_table()
        io.pfrio_table.pfrt_name = table.encode("ascii")
        io.pfrio_esize = ctypes.sizeof(pfr_addr)
        io.pfrio_size = 0
        self._call("GETADDRS", table, DIOCRGETADDRS, io)
        count = io.pfrio_size
        if count <= 0:
            return []
        cap = min(count + 64, PF_IOCTL_MAXCOUNT)
        arr = (pfr_addr * cap)()
        io2 = pfioc_table()
        io2.pfrio_table.pfrt_name = table.encode("ascii")
        io2.pfrio_esize = ctypes.sizeof(pfr_addr)
        io2.pfrio_size = cap
        io2.pfrio_buffer = ctypes.addressof(arr)
        self._call("GETADDRS", table, DIOCRGETADDRS, io2)
        n = min(max(io2.pfrio_size, 0), cap)
        return [_from_addr(arr[i]) for i in range(n)]
