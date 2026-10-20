import ipaddress
import random
import struct
import unittest

from appalias_testutil import (TYPE_A, TYPE_AAAA, TYPE_CNAME, TYPE_TXT, build_query,
                               build_response, encode_name)

from lib import dnswire


class ParseTests(unittest.TestCase):
    def test_simple_a(self):
        msg = build_response("www.example.com", [("www.example.com", TYPE_A, 120, "192.0.2.1")])
        resp = dnswire.parse_response(msg)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.qname, "www.example.com")
        self.assertEqual(resp.qtype, TYPE_A)
        self.assertEqual(len(resp.answers), 1)
        rec = resp.answers[0]
        self.assertEqual(rec.data, ipaddress.IPv4Address("192.0.2.1"))
        self.assertEqual(rec.ttl, 120)

    def test_compression_and_aaaa(self):
        answers = [("example.com", TYPE_A, 60, "192.0.2.2"),
                   ("example.com", TYPE_AAAA, 60, "2001:db8::2")]
        compressed = build_response("example.com", answers, compress=True)
        plain = build_response("example.com", answers, compress=False)
        self.assertLess(len(compressed), len(plain))
        for msg in (compressed, plain):
            resp = dnswire.parse_response(msg)
            self.assertEqual([r.data for r in resp.answers],
                             [ipaddress.ip_address("192.0.2.2"), ipaddress.ip_address("2001:db8::2")])

    def test_cname_chain(self):
        msg = build_response("app.example.com", [
            ("app.example.com", TYPE_CNAME, 300, "edge.cdn.example.net"),
            ("edge.cdn.example.net", TYPE_CNAME, 300, "a1.cdn.example.net"),
            ("a1.cdn.example.net", TYPE_A, 20, "198.51.100.7"),
            ("unrelated.example.org", TYPE_A, 20, "198.51.100.99"),
        ])
        resp = dnswire.parse_response(msg)
        chain = dnswire.cname_chain(resp)
        self.assertEqual(chain, ["app.example.com", "edge.cdn.example.net", "a1.cdn.example.net"])
        addrs = dnswire.addresses(resp, chain)
        self.assertEqual(addrs, [(ipaddress.ip_address("198.51.100.7"), 20)])

    def test_cname_loop_bounded(self):
        msg = build_response("a.example.com", [
            ("a.example.com", TYPE_CNAME, 60, "b.example.com"),
            ("b.example.com", TYPE_CNAME, 60, "a.example.com"),
        ])
        resp = dnswire.parse_response(msg)
        self.assertEqual(dnswire.cname_chain(resp), ["a.example.com", "b.example.com"])

    def test_case_and_other_types(self):
        msg = build_response("WWW.Example.COM", [
            ("WWW.Example.COM", TYPE_TXT, 60, b"\x03abc"),
            ("www.example.com", TYPE_A, 60, "192.0.2.3"),
        ], compress=False)
        resp = dnswire.parse_response(msg)
        self.assertEqual(resp.qname, "www.example.com")
        self.assertEqual(len(resp.answers), 1)

    def test_truncated_flag(self):
        msg = build_response("example.com", [], tc=True)
        resp = dnswire.parse_response(msg)
        self.assertTrue(resp.truncated)
        self.assertEqual(resp.answers, [])

    def test_high_ttl_is_zero(self):
        msg = build_response("example.com", [("example.com", TYPE_A, 0x80000001, "192.0.2.4")])
        self.assertEqual(dnswire.parse_response(msg).answers[0].ttl, 0)

    def test_query_is_not_response(self):
        self.assertIsNone(dnswire.parse_response(build_query("example.com")))


class MalformedTests(unittest.TestCase):
    def assertNone(self, msg):
        self.assertIsNone(dnswire.parse_response(msg))

    def test_empty_and_short(self):
        self.assertNone(b"")
        self.assertNone(b"\x00" * 11)
        self.assertNone(None)
        self.assertNone("texte")

    def test_truncated_everywhere(self):
        msg = build_response("www.example.com", [
            ("www.example.com", TYPE_CNAME, 60, "x.example.net"),
            ("x.example.net", TYPE_A, 60, "192.0.2.5"),
        ])
        for cut in range(len(msg)):
            # aucune exception, quel que soit l'endroit de la coupure
            dnswire.parse_response(msg[:cut])

    def test_pointer_loop(self):
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        msg = header + b"\xc0\x0c" + struct.pack("!HH", 1, 1)
        self.assertNone(msg)

    def test_too_many_jumps(self):
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        # chaîne de 20 pointeurs, chacun vers le précédent, terminée par un nom réel
        body = bytearray(encode_name("example.com"))
        base = len(header)
        offsets = [base]
        for _ in range(20):
            offsets.append(base + len(body))
            body += struct.pack("!H", 0xC000 | offsets[-2])
        msg = header + bytes(body)
        with self.assertRaises(dnswire.DnsError):
            dnswire.read_name(msg, offsets[-1])
        name, _ = dnswire.read_name(msg, offsets[5])
        self.assertEqual(name, "example.com")

    def test_pointer_out_of_bounds(self):
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        self.assertNone(header + b"\xff\xff" + struct.pack("!HH", 1, 1))

    def test_bad_rdlength(self):
        msg = bytearray(build_response("example.com", [("example.com", TYPE_A, 60, "192.0.2.6")]))
        # rdlength du A passé à 5 : hors message / taille invalide
        struct.pack_into("!H", msg, len(msg) - 6, 5)
        self.assertNone(bytes(msg))

    def test_too_many_records(self):
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 600, 0, 0)
        self.assertNone(header + encode_name("example.com") + struct.pack("!HH", 1, 1))

    def test_oversize(self):
        self.assertNone(b"\x00" * (dnswire.MAX_MESSAGE + 1))

    def test_name_too_long(self):
        long_name = ".".join(["a" * 63] * 5)
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        self.assertNone(header + encode_name(long_name) + struct.pack("!HH", 1, 1))

    def test_label_with_dot(self):
        header = struct.pack("!HHHHHH", 1, 0x8180, 1, 0, 0, 0)
        name = b"\x07evil.co\x03com\x00"  # label « evil.co » : contient un point
        msg = header + name + struct.pack("!HH", 1, 1)
        with self.assertRaises(dnswire.DnsError):
            dnswire.read_name(msg, 12)
        self.assertNone(msg)
        self.assertIsNone(dnswire.question_key(msg))

    def test_random_garbage(self):
        rnd = random.Random(42)
        base = build_response("www.example.com", [("www.example.com", TYPE_A, 60, "192.0.2.7")])
        for _ in range(2000):
            data = bytearray(base)
            for _ in range(rnd.randint(1, 6)):
                data[rnd.randrange(len(data))] = rnd.randrange(256)
            dnswire.parse_response(bytes(data))
            dnswire.parse_response(bytes(rnd.randrange(256) for _ in range(rnd.randint(0, 80))))


class QuestionKeyTests(unittest.TestCase):
    def test_question_key(self):
        self.assertEqual(dnswire.question_key(build_query("WWW.Example.com.", qid=7)),
                         (7, "www.example.com"))
        resp = build_response("www.example.com", [("www.example.com", TYPE_A, 60, "192.0.2.1")], qid=9)
        self.assertEqual(dnswire.question_key(resp), (9, "www.example.com"))
        self.assertIsNone(dnswire.question_key(b"\x00" * 5))
        self.assertIsNone(dnswire.question_key(build_query("example.com")[:-3]))


class ServfailTests(unittest.TestCase):
    def test_servfail(self):
        q = build_query("example.com", qid=0xBEEF)
        resp = dnswire.make_servfail(q)
        qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", resp[:12])
        self.assertEqual(qid, 0xBEEF)
        self.assertTrue(flags & 0x8000)
        self.assertEqual(flags & 0xF, 2)
        self.assertEqual(qd, 1)
        self.assertEqual(resp[12:], q[12:])
        self.assertIsNone(dnswire.make_servfail(b"\x00"))


class BuildQueryTests(unittest.TestCase):
    def test_roundtrip(self):
        msg = dnswire.build_query("WWW.Example.com.", "A", qid=0x4242)
        qid, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", msg[:12])
        self.assertEqual((qid, qd, an, ns, ar), (0x4242, 1, 0, 0, 1))
        self.assertEqual(flags, 0x0100)  # RD seul, pas de QR
        resp = dnswire.parse_message(msg)
        self.assertEqual((resp.id, resp.qname, resp.qtype), (0x4242, "www.example.com", TYPE_A))
        self.assertFalse(resp.is_response)
        self.assertEqual(resp.rcode, 0)
        self.assertIsNone(dnswire.parse_response(msg))

    def test_aaaa_and_int_type(self):
        for qtype in ("AAAA", "aaaa", TYPE_AAAA):
            msg = dnswire.build_query("example.com", qtype, qid=1)
            self.assertEqual(dnswire.parse_message(msg).qtype, TYPE_AAAA)
        self.assertEqual(dnswire.parse_message(dnswire.build_query("example.com", TYPE_A)).qtype, TYPE_A)

    def test_opt_1232(self):
        msg = dnswire.build_query("example.com", "A", qid=5)
        self.assertEqual(dnswire.udp_payload_size(msg), 1232)
        # OPT en fin de message : racine, type 41, classe 1232, TTL 0, rdlength 0
        self.assertEqual(msg[-11:], b"\x00" + struct.pack("!HHIH", 41, 1232, 0, 0))

    def test_question_key_matches_response(self):
        msg = dnswire.build_query("www.example.com", "A", qid=0x0BAD)
        resp = build_response("www.example.com", [("www.example.com", TYPE_A, 60, "192.0.2.1")],
                              qid=0x0BAD)
        self.assertEqual(dnswire.question_key(msg), dnswire.question_key(resp))
        self.assertEqual(dnswire.question_key(msg), (0x0BAD, "www.example.com"))

    def test_same_wire_as_testutil(self):
        msg = dnswire.build_query("example.com", "A", qid=0x1234)
        self.assertEqual(msg[12:-11], encode_name("example.com") + struct.pack("!HH", TYPE_A, 1))

    def test_qid_forced_and_random(self):
        for qid in (0, 1, 0xFFFF):
            self.assertEqual(dnswire.parse_message(dnswire.build_query("a.example", "A", qid=qid)).id, qid)
        ids = {dnswire.parse_message(dnswire.build_query("a.example", "A")).id for _ in range(64)}
        self.assertGreater(len(ids), 1)
        self.assertTrue(all(0 <= i <= 0xFFFF for i in ids))

    def test_name_forms(self):
        self.assertEqual(dnswire.build_query("example.com.", "A", qid=1),
                         dnswire.build_query("example.com", "A", qid=1))
        root = dnswire.build_query(".", "A", qid=1)
        self.assertEqual(dnswire.parse_message(root).qname, "")
        label63 = "a" * 63
        dnswire.build_query(label63 + ".example", "A")
        # 253 octets en notation pointée : limite acceptée
        name253 = ".".join(["a" * 61] * 4 + ["b" * 5])
        self.assertEqual(len(name253), 253)
        self.assertEqual(dnswire.parse_message(dnswire.build_query(name253, "A")).qname, name253)

    def test_invalid_names(self):
        too_long = ".".join(["a" * 61] * 4 + ["b" * 6])  # 254 caractères
        self.assertEqual(len(too_long), 254)
        for bad in ("", "a..b", ".example.com", "a" * 64 + ".com", too_long,
                    "caf\u00e9.example", "a\x00b.example", "a\tb.example", "a b.example",
                    "a\x7f.example", "..", None, b"example.com", 12):
            with self.subTest(name=bad):
                with self.assertRaises(ValueError):
                    dnswire.build_query(bad, "A")

    def test_invalid_type_and_qid(self):
        for bad in ("TXT", "", 0, 65536, -1, None, 1.5, True):
            with self.subTest(qtype=bad):
                with self.assertRaises(ValueError):
                    dnswire.build_query("example.com", bad)
        for bad in (-1, 65536, "1", 1.5, True):
            with self.subTest(qid=bad):
                with self.assertRaises(ValueError):
                    dnswire.build_query("example.com", "A", qid=bad)



def add_opt(msg, do=False, payload=1232):
    """Ajoute un OPT à la fin du message et incrémente ARCOUNT."""
    ttl = 0x8000 if do else 0
    out = bytearray(msg + b"\x00" + dnswire.RR_FIXED.pack(41, payload, ttl, 0))
    count = struct.unpack_from("!H", out, 10)[0]
    struct.pack_into("!H", out, 10, count + 1)
    return bytes(out)


def add_authority(msg, rtype, ttl, rdata=b""):
    """Insère un enregistrement d'autorité (nom racine) avant l'additionnel (aucun ici)."""
    out = bytearray(msg + b"\x00" + dnswire.RR_FIXED.pack(rtype, 1, ttl, len(rdata)) + rdata)
    count = struct.unpack_from("!H", out, 8)[0]
    struct.pack_into("!H", out, 8, count + 1)
    return bytes(out)


def soa_rdata(minimum):
    """rdata SOA minimal : mname et rname racine, puis les cinq compteurs."""
    return b"\x00\x00" + struct.pack("!IIIII", 1, 3600, 600, 86400, minimum)


def set_rcode(msg, rcode):
    out = bytearray(msg)
    flags = struct.unpack_from("!H", out, 2)[0]
    struct.pack_into("!H", out, 2, (flags & 0xFFF0) | rcode)
    return bytes(out)


class CacheHelpersTests(unittest.TestCase):
    def setUp(self):
        self.resp = add_opt(build_response("www.example.com", [
            ("www.example.com", TYPE_CNAME, 300, "edge.example.net"),
            ("edge.example.net", TYPE_A, 120, "192.0.2.1")]))

    def test_question_span(self):
        q = build_query("www.example.com")
        self.assertEqual(dnswire.question_span(q), (12, len(q)))
        self.assertIsNone(dnswire.question_span(b"\x00" * 5))
        self.assertIsNone(dnswire.question_span(q[:-2]))
        two = bytearray(q)
        struct.pack_into("!H", two, 4, 2)
        self.assertIsNone(dnswire.question_span(bytes(two)))
        compressed = q[:12] + b"\xc0\x0c" + q[-4:]
        self.assertIsNone(dnswire.question_span(compressed))

    def test_walk_records(self):
        recs = dnswire.walk_records(self.resp)
        self.assertEqual([(r[0], r[3]) for r in recs], [("an", 5), ("an", 1), ("ar", 41)])
        self.assertEqual([r[5] for r in recs], [300, 120, 0])
        for sec, name_off, fixed, rtype, rclass, ttl, rdlen in recs:
            self.assertEqual(dnswire.RR_FIXED.unpack_from(self.resp, fixed), (rtype, rclass, ttl, rdlen))
        with self.assertRaises(dnswire.DnsError):
            dnswire.walk_records(self.resp[:-3])
        big = bytearray(self.resp)
        struct.pack_into("!H", big, 6, 600)
        with self.assertRaises(dnswire.DnsError):
            dnswire.walk_records(bytes(big))

    def test_edns_info(self):
        q = build_query("example.com")
        self.assertEqual(dnswire.edns_info(q), (False, 512, False))
        self.assertEqual(dnswire.edns_info(add_opt(q, do=False)), (True, 1232, False))
        self.assertEqual(dnswire.edns_info(add_opt(q, do=True, payload=4096)), (True, 4096, True))
        self.assertEqual(dnswire.edns_info(q[:-1] + b""), (False, 512, False))

    def test_cache_key(self):
        a = dnswire.cache_key(build_query("WwW.Example.COM", TYPE_AAAA))
        self.assertEqual(a, ("www.example.com", TYPE_AAAA, 1, False))
        self.assertEqual(dnswire.cache_key(build_query("www.example.com", TYPE_AAAA, qid=9)), a)
        self.assertTrue(dnswire.cache_key(add_opt(build_query("example.com"), do=True))[3])
        self.assertIsNone(dnswire.cache_key(self.resp))
        self.assertIsNone(dnswire.cache_key(b"abc"))
        status = bytearray(build_query("example.com"))
        status[2] |= 0x28  # opcode 5
        self.assertIsNone(dnswire.cache_key(bytes(status)))

    def test_response_ttl_positive(self):
        self.assertEqual(dnswire.response_ttl(self.resp), 120)

    def test_response_ttl_huge_counts_zero(self):
        msg = build_response("example.com", [("example.com", TYPE_A, 0x80000000, "192.0.2.1")])
        self.assertEqual(dnswire.response_ttl(msg), 0)

    def test_response_ttl_negative(self):
        base = set_rcode(build_response("nx.example.com", []), 3)
        self.assertEqual(dnswire.response_ttl(add_authority(base, dnswire.TYPE_SOA, 3600,
                                                            soa_rdata(3600))), 300)
        self.assertEqual(dnswire.response_ttl(add_authority(base, dnswire.TYPE_SOA, 30,
                                                            soa_rdata(3600))), 30)
        # min(TTL du SOA, champ MINIMUM)
        self.assertEqual(dnswire.response_ttl(add_authority(base, dnswire.TYPE_SOA, 200,
                                                            soa_rdata(45))), 45)
        nodata = build_response("example.com", [], qtype=TYPE_AAAA)
        self.assertEqual(dnswire.response_ttl(add_authority(nodata, dnswire.TYPE_SOA, 120,
                                                            soa_rdata(900))), 120)

    def test_response_ttl_negative_sans_soa(self):
        # RFC 2308 : sans SOA, une réponse négative n'est pas cachable
        base = set_rcode(build_response("nx.example.com", []), 3)
        self.assertIsNone(dnswire.response_ttl(base))
        self.assertIsNone(dnswire.response_ttl(build_response("example.com", [], qtype=TYPE_AAAA)))
        # SOA illisible (rdata trop court) : ignoré
        self.assertIsNone(dnswire.response_ttl(add_authority(base, dnswire.TYPE_SOA, 30)))

    def test_cache_key_rd_cd(self):
        q = bytearray(build_query("www.example.com"))
        self.assertIsNotNone(dnswire.cache_key(bytes(q)))
        no_rd = bytearray(q)
        no_rd[2] &= 0xFE                       # RD=0
        self.assertIsNone(dnswire.cache_key(bytes(no_rd)))
        cd = bytearray(q)
        cd[3] |= 0x10                          # CD=1
        self.assertIsNone(dnswire.cache_key(bytes(cd)))

    def test_cache_key_minuscules_ascii(self):
        upper = build_query("www.example.com")
        a = dnswire.cache_key(upper.replace(b"www", b"\xc0\xc0x"))
        b = dnswire.cache_key(upper.replace(b"www", b"\xe0\xe0x"))
        self.assertIsNotNone(a)
        self.assertNotEqual(a, b)              # octets > 0x7F non repliés
        self.assertEqual(dnswire.cache_key(build_query("WWW.EXAMPLE.COM")),
                         dnswire.cache_key(build_query("www.example.com")))

    def test_question_tuple(self):
        resp = build_response("WwW.Example.com", [], qtype=TYPE_AAAA)
        self.assertEqual(dnswire.question_tuple(resp), ("www.example.com", TYPE_AAAA, 1))
        self.assertIsNone(dnswire.question_tuple(b"abc"))

    def test_response_ttl_refused(self):
        self.assertIsNone(dnswire.response_ttl(build_response("a.example.com", [], tc=True)))
        self.assertIsNone(dnswire.response_ttl(set_rcode(build_response("a.example.com", []), 2)))
        self.assertIsNone(dnswire.response_ttl(build_query("a.example.com")))
        self.assertIsNone(dnswire.response_ttl(b"\x00\x01"))

    def test_rewrite_basic(self):
        query = add_opt(build_query("WWW.example.COM", qid=0xBEEF))
        out = dnswire.rewrite_for_client(query, self.resp, 50)
        self.assertEqual(out[:2], b"\xbe\xef")
        self.assertEqual(dnswire.raw_question(out), dnswire.raw_question(query))
        self.assertIn(b"\x03WWW", out)
        recs = dnswire.walk_records(out)
        self.assertEqual([r[5] for r in recs], [250, 70, 0])  # l'OPT garde son TTL
        self.assertEqual(len(out), len(self.resp))

    def test_rewrite_ttl_floor(self):
        query = add_opt(build_query("www.example.com"))
        out = dnswire.rewrite_for_client(query, self.resp, 1000)
        self.assertEqual([r[5] for r in dnswire.walk_records(out)], [0, 0, 0])

    def test_rewrite_strips_opt(self):
        query = build_query("www.example.com", qid=7)
        out = dnswire.rewrite_for_client(query, self.resp, 10)
        self.assertEqual(len(out), len(self.resp) - 11)
        self.assertEqual(struct.unpack_from("!HHHHHH", out, 0)[5], 0)
        resp = dnswire.parse_response(out)
        orig = dnswire.parse_response(self.resp)
        self.assertEqual(resp.id, 7)
        self.assertEqual([r.data for r in resp.answers], [r.data for r in orig.answers])
        self.assertEqual([r.ttl for r in resp.answers], [290, 110])

    def test_rewrite_keeps_opt_if_not_last(self):
        resp = add_authority(self.resp, 2, 100)  # NS après l'OPT : l'OPT n'est plus dernier
        query = build_query("www.example.com")
        out = dnswire.rewrite_for_client(query, resp, 0)
        self.assertEqual(struct.unpack_from("!H", out, 10)[0], 1)
        self.assertEqual(len(out), len(resp))

    def test_rewrite_never_raises(self):
        query = build_query("www.example.com", qid=0x0102)
        self.assertEqual(dnswire.rewrite_for_client(query, self.resp[:-5], 0)[:2], b"\x01\x02")
        self.assertEqual(dnswire.rewrite_for_client(b"", b"\x00" * 12, 0), b"\x00" * 10)


if __name__ == "__main__":
    unittest.main()
