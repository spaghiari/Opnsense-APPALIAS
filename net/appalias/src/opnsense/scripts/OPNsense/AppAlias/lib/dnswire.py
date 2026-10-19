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

"""Lecture bornée des messages DNS (RFC 1035).

Seuls la question et la section réponse sont lues : enregistrements A, AAAA
et CNAME, avec la compression de noms. Les autres types sont ignorés.

Bornes :
- message de MAX_MESSAGE octets au plus ;
- MAX_RECORDS enregistrements (question + réponses) au plus ;
- MAX_POINTER_JUMPS sauts de compression au plus par nom.

parse_response() ne lève jamais d'exception : en cas d'erreur de lecture,
elle renvoie None et l'appelant transmet la réponse sans rien en tirer.

fit_udp() ramène une réponse obtenue par un flux (DNS over TLS) à la taille
UDP annoncée par le client (EDNS0, 512 octets sans EDNS) : au-delà, réponse
tronquée (TC=1, question seule), le client réessaie en TCP.
"""

import ipaddress
import secrets
import struct
from dataclasses import dataclass, field

TYPE_A = 1
TYPE_CNAME = 5
TYPE_AAAA = 28
CLASS_IN = 1

MAX_MESSAGE = 65535
MAX_RECORDS = 512
MAX_POINTER_JUMPS = 16
MAX_NAME_LENGTH = 255
MAX_LABEL_LENGTH = 63
MAX_CNAME_CHAIN = 16

FLAG_QR = 0x8000
FLAG_TC = 0x0200
FLAG_RD = 0x0100

HEADER = struct.Struct("!HHHHHH")
RR_FIXED = struct.Struct("!HHIH")
Q_FIXED = struct.Struct("!HH")


class DnsError(Exception):
    """Message DNS illisible ou hors bornes."""


@dataclass(frozen=True)
class Record:
    name: str
    rtype: int
    rclass: int
    ttl: int
    data: object  # IPv4Address, IPv6Address ou str (cible CNAME)


@dataclass
class Response:
    id: int
    flags: int
    qname: str
    qtype: int
    answers: list = field(default_factory=list)

    @property
    def is_response(self):
        return bool(self.flags & FLAG_QR)

    @property
    def truncated(self):
        return bool(self.flags & FLAG_TC)

    @property
    def rcode(self):
        return self.flags & 0x000F


def normalize_name(name):
    """Minuscules, sans point final ni espaces."""
    if not isinstance(name, str):
        return ""
    return name.strip().lower().rstrip(".")


def read_name(msg, offset):
    """Lit un nom (avec compression) à partir de offset.

    Renvoie (nom normalisé, offset suivant le nom dans le flux d'origine).
    Lève DnsError si le nom est malformé ou hors bornes.
    """
    labels = []
    length = 0
    jumps = 0
    end_offset = None
    pos = offset
    size = len(msg)
    while True:
        if pos >= size:
            raise DnsError("nom tronqué")
        first = msg[pos]
        kind = first & 0xC0
        if kind == 0xC0:
            if pos + 1 >= size:
                raise DnsError("pointeur de compression tronqué")
            jumps += 1
            if jumps > MAX_POINTER_JUMPS:
                raise DnsError("trop de sauts de compression")
            target = ((first & 0x3F) << 8) | msg[pos + 1]
            if end_offset is None:
                end_offset = pos + 2
            if target >= size:
                raise DnsError("pointeur hors message")
            pos = target
            continue
        if kind != 0:
            raise DnsError("type de label non pris en charge")
        if first == 0:
            if end_offset is None:
                end_offset = pos + 1
            break
        start = pos + 1
        stop = start + first
        if stop > size:
            raise DnsError("label tronqué")
        length += first + 1
        if length > MAX_NAME_LENGTH:
            raise DnsError("nom trop long")
        label = msg[start:stop]
        if b"." in label:
            raise DnsError("label contenant un point")
        labels.append(label.decode("latin-1"))
        pos = stop
    return normalize_name(".".join(labels)), end_offset


def parse_message(msg):
    """Analyse un message ; lève DnsError en cas de problème."""
    if not isinstance(msg, (bytes, bytearray, memoryview)):
        raise DnsError("type de message invalide")
    msg = bytes(msg)
    if len(msg) > MAX_MESSAGE:
        raise DnsError("message trop grand")
    if len(msg) < HEADER.size:
        raise DnsError("en-tête tronqué")
    qid, flags, qdcount, ancount, _nscount, _arcount = HEADER.unpack_from(msg, 0)
    if qdcount != 1:
        raise DnsError("une seule question attendue")
    if qdcount + ancount > MAX_RECORDS:
        raise DnsError("trop d'enregistrements")
    pos = HEADER.size
    qname, pos = read_name(msg, pos)
    if pos + Q_FIXED.size > len(msg):
        raise DnsError("question tronquée")
    qtype, _qclass = Q_FIXED.unpack_from(msg, pos)
    pos += Q_FIXED.size
    resp = Response(id=qid, flags=flags, qname=qname, qtype=qtype)
    for _ in range(ancount):
        name, pos = read_name(msg, pos)
        if pos + RR_FIXED.size > len(msg):
            raise DnsError("enregistrement tronqué")
        rtype, rclass, ttl, rdlength = RR_FIXED.unpack_from(msg, pos)
        pos += RR_FIXED.size
        rdstart = pos
        rdend = pos + rdlength
        if rdend > len(msg):
            raise DnsError("rdata tronqué")
        # RFC 2181 §8 : un TTL avec le bit de poids fort est traité comme 0.
        if ttl > 0x7FFFFFFF:
            ttl = 0
        data = None
        if rclass == CLASS_IN and rtype == TYPE_A:
            if rdlength != 4:
                raise DnsError("A de taille invalide")
            data = ipaddress.IPv4Address(msg[rdstart:rdend])
        elif rclass == CLASS_IN and rtype == TYPE_AAAA:
            if rdlength != 16:
                raise DnsError("AAAA de taille invalide")
            data = ipaddress.IPv6Address(msg[rdstart:rdend])
        elif rtype == TYPE_CNAME:
            target, tend = read_name(msg, rdstart)
            if tend > rdend:
                raise DnsError("CNAME déborde de son rdata")
            data = target
        if data is not None:
            resp.answers.append(Record(name, rtype, rclass, ttl, data))
        pos = rdend
    return resp


def question_key(msg):
    """(id, qname normalisé) de la question d'un message (requête ou réponse), ou None."""
    try:
        msg = bytes(msg)
        if len(msg) < HEADER.size or len(msg) > MAX_MESSAGE:
            return None
        qid, _flags, qdcount, _an, _ns, _ar = HEADER.unpack_from(msg, 0)
        if qdcount != 1:
            return None
        qname, pos = read_name(msg, HEADER.size)
        if pos + Q_FIXED.size > len(msg):
            return None
        return (qid, qname)
    except Exception:
        return None


def parse_response(msg):
    """Comme parse_message, mais renvoie None au lieu de lever."""
    try:
        resp = parse_message(msg)
    except (DnsError, ValueError, IndexError, struct.error, TypeError):
        return None
    except Exception:  # garde-fou : rien ne doit remonter au relais
        return None
    if not resp.is_response:
        return None
    return resp


def cname_chain(resp):
    """Noms de la chaîne CNAME en partant du qname (qname inclus)."""
    if resp is None or not resp.qname:
        return [] if resp is None else [resp.qname]
    targets = {}
    for rec in resp.answers:
        if rec.rtype == TYPE_CNAME and rec.name not in targets:
            targets[rec.name] = rec.data
    chain = [resp.qname]
    seen = {resp.qname}
    current = resp.qname
    while current in targets and len(chain) <= MAX_CNAME_CHAIN:
        nxt = targets[current]
        if not nxt or nxt in seen:
            break
        chain.append(nxt)
        seen.add(nxt)
        current = nxt
    return chain


def addresses(resp, names):
    """Adresses A/AAAA dont le propriétaire appartient à names.

    Renvoie une liste de (adresse ipaddress, ttl) sans doublon (TTL max gardé).
    """
    if resp is None:
        return []
    wanted = set(names)
    best = {}
    order = []
    for rec in resp.answers:
        if rec.rtype not in (TYPE_A, TYPE_AAAA) or rec.name not in wanted:
            continue
        if rec.data in best:
            best[rec.data] = max(best[rec.data], rec.ttl)
        else:
            best[rec.data] = rec.ttl
            order.append(rec.data)
    return [(ip, best[ip]) for ip in order]


TYPE_OPT = 41
UDP_MIN_PAYLOAD = 512
UDP_MAX_PAYLOAD = 4096
QUERY_UDP_PAYLOAD = 1232  # taille UDP annoncée par build_query (évite la fragmentation IP)

QUERY_TYPES = {"A": TYPE_A, "AAAA": TYPE_AAAA}


def udp_payload_size(query):
    """Taille de réponse UDP acceptée par le client qui a envoyé query.

    Classe de l'enregistrement OPT de la section additionnelle (EDNS0,
    RFC 6891 §6.2.3 et §6.2.5), 512 au moins ; 512 sans EDNS ou si la
    requête est illisible (RFC 1035 §4.2.1).
    """
    try:
        query = bytes(query)
        if len(query) < HEADER.size or len(query) > MAX_MESSAGE:
            return UDP_MIN_PAYLOAD
        _qid, _flags, qdcount, ancount, nscount, arcount = HEADER.unpack_from(query, 0)
        if qdcount + ancount + nscount + arcount > MAX_RECORDS:
            return UDP_MIN_PAYLOAD
        pos = HEADER.size
        for _ in range(qdcount):
            _name, pos = read_name(query, pos)
            pos += Q_FIXED.size
        for index in range(ancount + nscount + arcount):
            _name, pos = read_name(query, pos)
            if pos + RR_FIXED.size > len(query):
                return UDP_MIN_PAYLOAD
            rtype, rclass, _ttl, rdlength = RR_FIXED.unpack_from(query, pos)
            if index >= ancount + nscount and rtype == TYPE_OPT:
                # plafond : au-delà de net.inet.udp.maxdgram (9216 sur FreeBSD),
                # sendto échoue en silence ; 4096 est la valeur usuelle
                return min(max(UDP_MIN_PAYLOAD, rclass), UDP_MAX_PAYLOAD)
            pos += RR_FIXED.size + rdlength
            if pos > len(query):
                return UDP_MIN_PAYLOAD
    except Exception:
        pass
    return UDP_MIN_PAYLOAD


def raw_question(msg):
    """Octets de la première question de msg (nom sans compression), ou None."""
    try:
        msg = bytes(msg)
        if len(msg) < HEADER.size or HEADER.unpack_from(msg, 0)[2] < 1:
            return None
        pos = HEADER.size
        while True:
            length = msg[pos]
            if length == 0:
                break
            if length & 0xC0:  # pointeur : absent d'une question bien formée en tête
                return None
            pos += 1 + length
        end = pos + 1 + Q_FIXED.size
        if end > len(msg) or end - HEADER.size > MAX_NAME_LENGTH + 1 + Q_FIXED.size:
            return None
        return msg[HEADER.size:end]
    except IndexError:
        return None


def truncate(query, resp):
    """Réponse tronquée valide : en-tête de resp avec TC=1, question seule.

    La question est celle de la requête du client (même casse), sinon celle de
    la réponse ; aucune ne se lit : en-tête seul.
    """
    resp = bytes(resp)
    qid, flags, _qd, _an, _ns, _ar = HEADER.unpack_from(resp, 0)
    question = raw_question(query)
    if question is None:
        question = raw_question(resp)
    count = 1 if question is not None else 0
    return HEADER.pack(qid, flags | FLAG_TC, count, 0, 0, 0) + (question or b"")


def fit_udp(query, resp):
    """resp si elle tient dans la taille UDP du client, sinon truncate(query, resp)."""
    if resp is None or len(resp) < HEADER.size or len(resp) <= udp_payload_size(query):
        return resp
    return truncate(query, resp)


def make_servfail(query):
    """Construit une réponse SERVFAIL minimale pour une requête, ou None."""
    try:
        query = bytes(query)
        if len(query) < HEADER.size:
            return None
        qid, flags, qdcount, _an, _ns, _ar = HEADER.unpack_from(query, 0)
        if flags & FLAG_QR:
            return None
        opcode_rd = flags & 0x7900  # opcode + RD
        new_flags = FLAG_QR | opcode_rd | 0x0080 | 2  # RA + SERVFAIL
        question = b""
        count = 0
        if qdcount == 1:
            try:
                _name, pos = read_name(query, HEADER.size)
                if pos + Q_FIXED.size <= len(query):
                    question = query[HEADER.size:pos + Q_FIXED.size]
                    count = 1
            except DnsError:
                pass
        return HEADER.pack(qid, new_flags, count, 0, 0, 0) + question
    except Exception:
        return None


def encode_name(name):
    """Nom en format fil (labels sans compression), terminé par la racine.

    Point final optionnel ; "." seul désigne la racine. Lève ValueError pour
    un label vide hors racine, un label de plus de 63 octets, un nom de plus
    de 255 octets une fois encodé (253 en notation pointée) ou un caractère
    hors ASCII imprimable.
    """
    if not isinstance(name, str):
        raise ValueError("nom invalide")
    if name == ".":
        return b"\x00"
    if name.endswith("."):
        name = name[:-1]
    out = bytearray()
    for label in name.split("."):
        if not label:
            raise ValueError("label vide")
        if any(not 0x21 <= ord(ch) <= 0x7E for ch in label):
            raise ValueError("caractère non ASCII imprimable dans le nom")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError("label trop long")
        out.append(len(label))
        out.extend(label.encode("ascii"))
    out.append(0)
    if len(out) > MAX_NAME_LENGTH:
        raise ValueError("nom trop long")
    return bytes(out)


def build_query(qname, qtype, qid=None):
    """Requête DNS standard : RD, une question IN, OPT EDNS0 (UDP 1232).

    qtype : entier (1 à 65535) ou "A"/"AAAA". qid : 16 bits, aléatoire
    (secrets) si None. Lève ValueError si l'un des paramètres est invalide.
    """
    if isinstance(qtype, str):
        code = QUERY_TYPES.get(qtype.strip().upper())
        if code is None:
            raise ValueError("type de requête non pris en charge")
        qtype = code
    elif isinstance(qtype, bool) or not isinstance(qtype, int) or not 1 <= qtype <= 0xFFFF:
        raise ValueError("type de requête invalide")
    if qid is None:
        qid = secrets.randbits(16)
    elif isinstance(qid, bool) or not isinstance(qid, int) or not 0 <= qid <= 0xFFFF:
        raise ValueError("identifiant de requête invalide")
    header = HEADER.pack(qid, FLAG_RD, 1, 0, 0, 1)
    question = encode_name(qname) + Q_FIXED.pack(qtype, CLASS_IN)
    # OPT (RFC 6891) : nom racine, type 41, classe = taille UDP, TTL 0, rdata vide
    opt = b"\x00" + RR_FIXED.pack(TYPE_OPT, QUERY_UDP_PAYLOAD, 0, 0)
    return header + question + opt
