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


if __name__ == "__main__":
    unittest.main()
