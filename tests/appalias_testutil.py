"""Outils communs aux tests : sys.path, construction de messages DNS, faux pfctl.

Importé en premier par chaque module de test : il ajoute au sys.path le
dossier scripts du plugin pour que « import lib.xxx » fonctionne.
"""

import sys

sys.dont_write_bytecode = True  # aucun .pyc, ni dans tests/ ni sous net/

import ipaddress
import logging
import os
import struct
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "net", "appalias", "src", "opnsense", "scripts", "OPNsense", "AppAlias")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

# Pas de sortie du journal « appalias » pendant les tests (assertLogs reste utilisable).
logging.getLogger("appalias").addHandler(logging.NullHandler())
logging.getLogger("appalias").propagate = False

TYPE_A = 1
TYPE_CNAME = 5
TYPE_AAAA = 28
TYPE_TXT = 16


def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        if label:
            raw = label.encode("ascii")
            out += bytes([len(raw)]) + raw
    return out + b"\x00"


def build_query(qname, qtype=TYPE_A, qid=0x1234):
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    return header + encode_name(qname) + struct.pack("!HH", qtype, 1)


def build_response(qname, answers, qtype=TYPE_A, qid=0x1234, tc=False, compress=True):
    """answers : liste de (nom, type, ttl, valeur). Compression : pointeurs
    vers les noms déjà écrits (suffixes compris)."""
    flags = 0x8180 | (0x0200 if tc else 0)
    msg = bytearray(struct.pack("!HHHHHH", qid, flags, 1, len(answers), 0, 0))
    offsets = {}

    def write_name(name):
        labels = [l for l in name.rstrip(".").split(".") if l]
        for i in range(len(labels)):
            suffix = ".".join(labels[i:]).lower()
            if compress and suffix in offsets:
                msg.extend(struct.pack("!H", 0xC000 | offsets[suffix]))
                return
            if len(msg) < 0x3FFF:
                offsets[suffix] = len(msg)
            raw = labels[i].encode("ascii")
            msg.append(len(raw))
            msg.extend(raw)
        msg.append(0)

    write_name(qname)
    msg.extend(struct.pack("!HH", qtype, 1))
    for name, rtype, ttl, value in answers:
        write_name(name)
        if rtype == TYPE_A:
            rdata = ipaddress.IPv4Address(value).packed
            msg.extend(struct.pack("!HHIH", rtype, 1, ttl, len(rdata)))
            msg.extend(rdata)
        elif rtype == TYPE_AAAA:
            rdata = ipaddress.IPv6Address(value).packed
            msg.extend(struct.pack("!HHIH", rtype, 1, ttl, len(rdata)))
            msg.extend(rdata)
        elif rtype == TYPE_CNAME:
            msg.extend(struct.pack("!HHIH", rtype, 1, ttl, 0))
            len_pos = len(msg) - 2
            start = len(msg)
            write_name(value)
            struct.pack_into("!H", msg, len_pos, len(msg) - start)
        else:
            rdata = bytes(value)
            msg.extend(struct.pack("!HHIH", rtype, 1, ttl, len(rdata)))
            msg.extend(rdata)
    return bytes(msg)


class FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakePf:
    """Faux pfctl injecté dans PfTable(runner=…) : garde les tables en mémoire."""

    def __init__(self, delay=0.0, fail=False):
        self.delay = delay
        self.fail = fail
        self.tables = {}
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, argv, timeout):
        args = argv[1:]
        table = args[args.index("-t") + 1]
        op = args[args.index("-T") + 1]
        rest = args[args.index("-T") + 2:]
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.calls.append((op, table, list(rest)))
            if self.fail:
                return FakeResult(1, "", "pfctl: Table does not exist.")
            entries = self.tables.setdefault(table, set())
            if op == "add":
                entries.update(rest)
            elif op == "delete":
                entries.difference_update(rest)
            elif op == "flush":
                entries.clear()
            elif op == "replace":
                path = rest[rest.index("-f") + 1]
                with open(path, "r", encoding="ascii") as fh:
                    entries.clear()
                    entries.update(l.strip() for l in fh if l.strip())
            elif op == "show":
                return FakeResult(0, "".join("   %s\n" % e for e in sorted(entries)), "")
        return FakeResult(0, "", "")

    def ops(self, op, table=None):
        with self.lock:
            return [c for c in self.calls if c[0] == op and (table is None or c[1] == table)]

    def contents(self, table):
        with self.lock:
            return set(self.tables.get(table, set()))
