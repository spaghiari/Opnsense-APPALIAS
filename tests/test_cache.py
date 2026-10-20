import struct
import unittest

from appalias_testutil import TYPE_A, TYPE_AAAA, build_query, build_response

from lib import dnswire
from lib.cache import Cache


def response(name="www.example.com", ttl=120, qid=0x1234, **kw):
    return build_response(name, [(name, TYPE_A, ttl, "192.0.2.1")], qid=qid, **kw)


class CacheTests(unittest.TestCase):
    def test_hit_after_put(self):
        cache = Cache()
        query = build_query("www.example.com", qid=0x4242)
        self.assertIsNone(cache.get(query, now=100))
        self.assertTrue(cache.put(build_query("www.example.com"), response(), now=100))
        out = cache.get(query, now=100)
        self.assertIsNotNone(out)
        self.assertEqual(struct.unpack_from("!H", out, 0)[0], 0x4242)
        parsed = dnswire.parse_response(out)
        self.assertEqual(str(parsed.answers[0].data), "192.0.2.1")
        self.assertEqual((cache.hits, cache.misses), (1, 1))
        self.assertEqual(len(cache), 1)

    def test_case_kept_for_client(self):
        cache = Cache()
        cache.put(build_query("www.example.com"), response(), now=0)
        out = cache.get(build_query("WwW.ExAmPlE.cOm"), now=0)
        self.assertIsNotNone(out)
        self.assertIn(b"WwW", out)

    def test_expiry(self):
        cache = Cache()
        cache.put(build_query("www.example.com"), response(ttl=30), now=100)
        self.assertIsNotNone(cache.get(build_query("www.example.com"), now=129))
        self.assertIsNone(cache.get(build_query("www.example.com"), now=130))
        self.assertEqual(len(cache), 0)

    def test_clock_injected(self):
        t = [1000.0]
        cache = Cache(clock=lambda: t[0])
        cache.put(build_query("www.example.com"), response(ttl=10))
        self.assertIsNotNone(cache.get(build_query("www.example.com")))
        t[0] += 11
        self.assertIsNone(cache.get(build_query("www.example.com")))

    def test_ttl_decremented(self):
        cache = Cache()
        cache.put(build_query("www.example.com"), response(ttl=120), now=100)
        out = cache.get(build_query("www.example.com"), now=145)
        self.assertEqual(dnswire.parse_response(out).answers[0].ttl, 75)

    def test_max_ttl(self):
        cache = Cache(max_ttl=60)
        cache.put(build_query("www.example.com"), response(ttl=3600), now=0)
        self.assertIsNotNone(cache.get(build_query("www.example.com"), now=59))
        self.assertIsNone(cache.get(build_query("www.example.com"), now=60))
        cache.max_ttl = 0
        self.assertFalse(cache.put(build_query("a.example.com"), response("a.example.com"), now=0))

    def test_zero_ttl_refused(self):
        cache = Cache()
        self.assertFalse(cache.put(build_query("www.example.com"), response(ttl=0), now=0))
        self.assertEqual(len(cache), 0)

    def test_eviction_expired_first_then_oldest(self):
        cache = Cache(max_entries=3)
        for name, ttl in (("a.example.com", 10), ("b.example.com", 1000), ("c.example.com", 1000)):
            self.assertTrue(cache.put(build_query(name), response(name, ttl), now=0))
        # a est expirée : elle part, pas b
        self.assertTrue(cache.put(build_query("d.example.com"), response("d.example.com"), now=50))
        self.assertEqual(len(cache), 3)
        self.assertIsNone(cache.get(build_query("a.example.com"), now=50))
        self.assertIsNotNone(cache.get(build_query("b.example.com"), now=50))
        # plus rien d'expiré : la plus ancienne (b) part
        self.assertTrue(cache.put(build_query("e.example.com"), response("e.example.com"), now=60))
        self.assertEqual(len(cache), 3)
        self.assertIsNone(cache.get(build_query("b.example.com"), now=60))
        for name in ("c", "d", "e"):
            self.assertIsNotNone(cache.get(build_query(name + ".example.com"), now=60))

    def test_put_replaces(self):
        cache = Cache(max_entries=2)
        q = build_query("www.example.com")
        cache.put(q, response(ttl=10), now=0)
        cache.put(q, response(ttl=500), now=0)
        self.assertEqual(len(cache), 1)
        self.assertIsNotNone(cache.get(q, now=100))

    def test_refused_responses(self):
        cache = Cache()
        q = build_query("www.example.com")
        self.assertFalse(cache.put(q, response(tc=True), now=0))
        self.assertFalse(cache.put(q, b"junk", now=0))
        # une "requête" qui est en fait une réponse n'a pas de clé
        self.assertFalse(cache.put(response(), response(), now=0))
        self.assertEqual(len(cache), 0)

    def test_question_length_mismatch_is_miss(self):
        cache = Cache()
        cache.put(build_query("www.example.com"), response(), now=0)
        key = dnswire.cache_key(build_query("www.example.com"))
        stored = cache._entries[key]
        cache._entries[key] = stored[:3] + (stored[3] + 1,)
        self.assertIsNone(cache.get(build_query("www.example.com"), now=0))

    def test_do_bit_separate_keys(self):
        cache = Cache()
        plain = build_query("www.example.com")
        opt = plain[:10] + struct.pack("!H", 1) + plain[12:]
        opt += bytes([0]) + dnswire.RR_FIXED.pack(41, 1232, 0x8000, 0)
        cache.put(plain, response(), now=0)
        self.assertIsNone(cache.get(opt, now=0))

    def test_disabled(self):
        cache = Cache(max_entries=0)
        self.assertFalse(cache.put(build_query("www.example.com"), response(), now=0))
        self.assertIsNone(cache.get(build_query("www.example.com"), now=0))
        self.assertEqual(len(cache), 0)

    def test_clear(self):
        cache = Cache()
        cache.put(build_query("www.example.com"), response(), now=0)
        cache.clear()
        self.assertEqual(len(cache), 0)
        self.assertIsNone(cache.get(build_query("www.example.com"), now=0))


def with_flags(msg, set_bits=0, clear_bits=0):
    flags = (struct.unpack_from("!H", msg, 2)[0] | set_bits) & ~clear_bits & 0xFFFF
    return msg[:2] + struct.pack("!H", flags) + msg[4:]


class CachePoisoningTests(unittest.TestCase):
    """0.9 : requêtes RD=0 / CD=1, question différente, bornes mémoire."""

    def test_cd_jamais_en_cache(self):
        cache = Cache()
        cd = with_flags(build_query("www.example.com"), set_bits=0x0010)
        self.assertFalse(cache.put(cd, response(), now=0))
        cache.put(build_query("www.example.com"), response(), now=0)
        self.assertIsNone(cache.get(cd, now=0))           # CD=1 : toujours l'amont
        self.assertIsNotNone(cache.get(build_query("www.example.com"), now=0))

    def test_rd0_jamais_en_cache(self):
        cache = Cache()
        no_rd = with_flags(build_query("www.example.com"), clear_bits=0x0100)
        self.assertFalse(cache.put(no_rd, build_response("www.example.com", []), now=0))
        self.assertEqual(len(cache), 0)
        cache.put(build_query("www.example.com"), response(), now=0)
        self.assertIsNone(cache.get(no_rd, now=0))

    def test_negative_sans_soa_refusee(self):
        cache = Cache()
        self.assertFalse(cache.put(build_query("www.example.com"),
                                   build_response("www.example.com", []), now=0))

    def test_question_differente_refusee(self):
        cache = Cache()
        q = build_query("www.example.com")
        self.assertFalse(cache.put(q, response("other.example.com"), now=0))       # nom
        aaaa = build_response("www.example.com", [("www.example.com", TYPE_AAAA, 60,
                                                    "2001:db8::1")], qtype=TYPE_AAAA)
        self.assertFalse(cache.put(q, aaaa, now=0))                                 # type
        other_class = bytearray(response())
        struct.pack_into("!H", other_class, len(q) - 2, 3)                          # classe CH
        self.assertFalse(cache.put(q, bytes(other_class), now=0))
        self.assertEqual(len(cache), 0)

    def test_plafond_par_entree(self):
        cache = Cache(max_entry_bytes=100)
        big = build_response("www.example.com", [("www.example.com", TYPE_A, 60, "192.0.2.%d" % i)
                                                 for i in range(1, 10)])
        self.assertGreater(len(big), 100)
        self.assertFalse(cache.put(build_query("www.example.com"), big, now=0))
        self.assertTrue(cache.put(build_query("www.example.com"), response(), now=0))

    def test_budget_octets(self):
        size = len(response("a.example.com"))
        cache = Cache(max_bytes=2 * size)
        for name in ("a", "b", "c"):
            self.assertTrue(cache.put(build_query(name + ".example.com"),
                                      response(name + ".example.com"), now=0))
        self.assertEqual(len(cache), 2)
        self.assertLessEqual(cache.bytes, 2 * size)
        self.assertIsNone(cache.get(build_query("a.example.com"), now=0))   # la plus ancienne
        self.assertIsNotNone(cache.get(build_query("c.example.com"), now=0))
        cache.clear()
        self.assertEqual(cache.bytes, 0)

    def test_compteur_octets_remplacement_expiration(self):
        cache = Cache()
        q = build_query("www.example.com")
        cache.put(q, response(ttl=10), now=0)
        cache.put(q, response(ttl=10), now=0)
        self.assertEqual(cache.bytes, len(response()))
        self.assertIsNone(cache.get(q, now=20))
        self.assertEqual(cache.bytes, 0)


if __name__ == "__main__":
    unittest.main()
