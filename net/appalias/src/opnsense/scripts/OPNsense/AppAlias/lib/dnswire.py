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
FLAG_CD = 0x0010

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


# --- Cache de réponses (0.9) : clé, TTL, réécriture pour le client ---------

TYPE_SOA = 6
NEGATIVE_TTL_MAX = 300      # plafond du TTL de cache d'une réponse négative (s)
# Plus utilisé depuis 0.9 : une réponse négative sans SOA n'est pas cachable
# (RFC 2308). Gardé pour la compatibilité des imports.
NEGATIVE_TTL_DEFAULT = 60
FLAG_DO = 0x8000            # bit DO (DNSSEC OK) du TTL de l'OPT


def question_span(msg):
    """(début, fin) de la section question de msg, ou None.

    None si le message est illisible, si qdcount != 1 ou si le nom de la
    question contient un pointeur de compression.
    """
    try:
        msg = bytes(msg)
        if len(msg) < HEADER.size or len(msg) > MAX_MESSAGE:
            return None
        if HEADER.unpack_from(msg, 0)[2] != 1:
            return None
        pos = HEADER.size
        total = 0
        while True:
            length = msg[pos]
            if length == 0:
                pos += 1
                break
            if length & 0xC0:
                return None
            total += length + 1
            if total > MAX_NAME_LENGTH:
                return None
            pos += 1 + length
        end = pos + Q_FIXED.size
        if end > len(msg):
            return None
        return (HEADER.size, end)
    except IndexError:
        return None


def walk_records(msg):
    """Parcourt les sections réponse, autorité et additionnel.

    Renvoie une liste de (section, name_offset, fixed_offset, rtype, rclass,
    ttl, rdlength) avec section dans "an", "ns", "ar". fixed_offset est
    l'offset de la partie fixe (type, classe, TTL, rdlength). Le TTL est la
    valeur brute du message. Lève DnsError si le message est malformé ou si
    le total d'enregistrements dépasse MAX_RECORDS.
    """
    if not isinstance(msg, (bytes, bytearray, memoryview)):
        raise DnsError("type de message invalide")
    msg = bytes(msg)
    if len(msg) > MAX_MESSAGE:
        raise DnsError("message trop grand")
    if len(msg) < HEADER.size:
        raise DnsError("en-tête tronqué")
    _qid, _flags, qdcount, ancount, nscount, arcount = HEADER.unpack_from(msg, 0)
    if qdcount + ancount + nscount + arcount > MAX_RECORDS:
        raise DnsError("trop d'enregistrements")
    pos = HEADER.size
    for _ in range(qdcount):
        _name, pos = read_name(msg, pos)
        pos += Q_FIXED.size
        if pos > len(msg):
            raise DnsError("question tronquée")
    out = []
    for section, count in (("an", ancount), ("ns", nscount), ("ar", arcount)):
        for _ in range(count):
            name_offset = pos
            _name, pos = read_name(msg, pos)
            if pos + RR_FIXED.size > len(msg):
                raise DnsError("enregistrement tronqué")
            rtype, rclass, ttl, rdlength = RR_FIXED.unpack_from(msg, pos)
            fixed_offset = pos
            pos += RR_FIXED.size + rdlength
            if pos > len(msg):
                raise DnsError("rdata tronqué")
            out.append((section, name_offset, fixed_offset, rtype, rclass, ttl, rdlength))
    return out


def edns_info(msg):
    """(présent, taille UDP annoncée, bit DO) d'après l'OPT de l'additionnel.

    (False, 512, False) sans OPT ou si le message est illisible.
    """
    try:
        for section, _no, _fo, rtype, rclass, ttl, _rd in walk_records(msg):
            if section == "ar" and rtype == TYPE_OPT:
                return (True, rclass, bool(ttl & FLAG_DO))
    except Exception:
        pass
    return (False, UDP_MIN_PAYLOAD, False)


def question_tuple(msg):
    """(qname, qtype, qclass) de la question de msg, lue octet par octet, ou None.

    Le nom est passé en minuscules sur l'ASCII seulement (bytes.lower) : les
    octets > 0x7F restent tels quels, deux noms qui ne diffèrent que par eux
    ne se confondent pas. None si la question est illisible, compressée,
    si qdcount != 1 ou si un label contient un point.
    """
    try:
        msg = bytes(msg)
        span = question_span(msg)
        if span is None:
            return None
        labels = []
        pos = span[0]
        end = span[1] - Q_FIXED.size
        while pos < end:
            length = msg[pos]
            if length == 0:
                break
            label = msg[pos + 1:pos + 1 + length]
            if b"." in label:
                return None
            labels.append(label.lower().decode("latin-1"))
            pos += 1 + length
        qtype, qclass = Q_FIXED.unpack_from(msg, end)
        return (".".join(labels), qtype, qclass)
    except Exception:
        return None


def cache_key(query):
    """(qname minuscule ASCII, qtype, qclass, do) d'une requête, ou None.

    None si le message est illisible, si qdcount != 1, si c'est une réponse
    (QR=1), si l'opcode n'est pas 0, si RD=0 (requête non récursive) ou si
    CD=1 (validation DNSSEC désactivée) : ces deux dernières passent
    toujours par l'amont et ne sont jamais mises en cache.
    """
    try:
        query = bytes(query)
        if len(query) < HEADER.size or len(query) > MAX_MESSAGE:
            return None
        _qid, flags, qdcount, _an, _ns, _ar = HEADER.unpack_from(query, 0)
        if (qdcount != 1 or flags & FLAG_QR or (flags >> 11) & 0xF
                or not flags & FLAG_RD or flags & FLAG_CD):
            return None
        question = question_tuple(query)
        if question is None:
            return None
        return question + (edns_info(query)[2],)
    except Exception:
        return None


def _effective_ttl(ttl):
    return 0 if ttl > 0x7FFFFFFF else ttl


def response_ttl(resp_bytes):
    """TTL de cache d'une réponse, ou None si elle n'est pas cachable.

    Non cachable : QR=0, TC=1, opcode != 0, rcode hors {0, 3}, qdcount != 1,
    message illisible. Réponse positive : plus petit TTL de la section
    réponse. Réponse négative (NXDOMAIN ou NODATA, section réponse vide) :
    cachable seulement si l'autorité contient un SOA lisible (RFC 2308) ;
    TTL = min(TTL du SOA, champ MINIMUM du SOA), plafonné à NEGATIVE_TTL_MAX.
    Les OPT sont ignorés ; un TTL > 0x7FFFFFFF compte 0 (RFC 2181).
    """
    try:
        msg = bytes(resp_bytes)
        if len(msg) < HEADER.size or len(msg) > MAX_MESSAGE:
            return None
        _qid, flags, qdcount, _an, _ns, _ar = HEADER.unpack_from(msg, 0)
        if (not flags & FLAG_QR or flags & FLAG_TC or (flags >> 11) & 0xF
                or flags & 0xF not in (0, 3) or qdcount != 1):
            return None
        records = walk_records(msg)
    except Exception:
        return None
    answers = [_effective_ttl(r[5]) for r in records if r[0] == "an" and r[3] != TYPE_OPT]
    if answers:
        return min(answers)
    soa = []
    for section, _no, fixed, rtype, _rc, ttl, rdlength in records:
        if section != "ns" or rtype != TYPE_SOA:
            continue
        # rdata : mname, rname, puis serial, refresh, retry, expire, minimum (u32).
        if rdlength < 22:
            continue
        end = fixed + RR_FIXED.size + rdlength
        minimum = struct.unpack_from("!I", msg, end - 4)[0]
        soa.append(min(_effective_ttl(ttl), _effective_ttl(minimum)))
    if soa:
        return min(min(soa), NEGATIVE_TTL_MAX)
    return None


def rewrite_for_client(query, cached, elapsed):
    """Réponse à rendre au client à partir d'une réponse mise en cache.

    ID et question viennent de la requête (casse 0x20 conservée), les TTL
    (hors OPT) sont réduits de elapsed sans passer sous 0, l'OPT est retiré
    si le client n'en a pas et s'il est le dernier enregistrement. Ne lève
    jamais : en cas de doute, cached avec seulement l'ID remplacé.
    """
    try:
        query = bytes(query)
        cached = bytes(cached)
        qid = query[:2]
        try:
            elapsed = max(int(elapsed), 0)
            qspan = question_span(query)
            cspan = question_span(cached)
            if qspan is None or cspan is None or qspan[1] - qspan[0] != cspan[1] - cspan[0]:
                return qid + cached[2:]
            records = walk_records(cached)
            msg = bytearray(cached)
            msg[0:2] = qid
            msg[cspan[0]:cspan[1]] = query[qspan[0]:qspan[1]]
            for _sec, _no, fixed, rtype, _rc, ttl, _rd in records:
                if rtype == TYPE_OPT:
                    continue
                struct.pack_into("!I", msg, fixed + 4, max(_effective_ttl(ttl) - elapsed, 0))
            if records and not edns_info(query)[0]:
                sec, name_off, _f, rtype, _rc, _t, _rd = records[-1]
                if sec == "ar" and rtype == TYPE_OPT:
                    del msg[name_off:]
                    arcount = HEADER.unpack_from(msg, 0)[5]
                    struct.pack_into("!H", msg, 10, arcount - 1)
            return bytes(msg)
        except Exception:
            return qid + cached[2:]
    except Exception:
        return cached
