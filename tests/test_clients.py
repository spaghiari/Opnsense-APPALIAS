import ipaddress
import unittest

import appalias_testutil  # noqa: F401 (sys.path)

from lib.clients import DEFAULT_CLIENT_NETWORKS, ClientAcl, RateLimiter


class AclTests(unittest.TestCase):
    def test_default(self):
        acl = ClientAcl()
        for ip in ("127.0.0.1", "::1", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1",
                   "100.64.0.1", "169.254.1.1", "fd00::1", "fe80::1"):
            with self.subTest(ip=ip):
                self.assertTrue(acl.allowed(ip))
        for ip in ("8.8.8.8", "172.32.0.1", "2001:4860:4860::8888", "100.128.0.1"):
            with self.subTest(ip=ip):
                self.assertFalse(acl.allowed(ip))
        self.assertEqual(len(acl.networks), len(DEFAULT_CLIENT_NETWORKS))

    def test_mapped_ipv4(self):
        acl = ClientAcl()
        self.assertTrue(acl.allowed("::ffff:192.168.1.5"))
        self.assertFalse(acl.allowed("::ffff:8.8.8.8"))
        self.assertTrue(acl.allowed(ipaddress.ip_address("::ffff:10.0.0.1")))

    def test_invalid(self):
        acl = ClientAcl()
        for ip in ("", "pas une ip", "999.1.1.1", None):
            self.assertFalse(acl.allowed(ip))

    def test_explicit(self):
        acl = ClientAcl([ipaddress.ip_network("203.0.113.0/24")])
        self.assertTrue(acl.allowed("203.0.113.9"))
        self.assertFalse(acl.allowed("10.0.0.1"))
        self.assertFalse(acl.allowed("127.0.0.1"))
        self.assertEqual(ClientAcl([]).networks, ClientAcl().networks)
        self.assertEqual(ClientAcl(None).networks, ClientAcl().networks)

    def test_object_input(self):
        self.assertTrue(ClientAcl().allowed(ipaddress.ip_address("192.168.0.1")))


class RateLimiterTests(unittest.TestCase):
    def test_burst_and_refill(self):
        rl = RateLimiter(2, burst=4)
        results = [rl.allow("192.168.1.1", now=0) for _ in range(5)]
        self.assertEqual(results, [True, True, True, True, False])
        self.assertEqual(rl.dropped, 1)
        self.assertTrue(rl.allow("192.168.1.1", now=1))
        self.assertTrue(rl.allow("192.168.1.1", now=1))
        self.assertFalse(rl.allow("192.168.1.1", now=1))
        self.assertEqual(rl.dropped, 2)

    def test_default_burst(self):
        rl = RateLimiter(3)
        self.assertEqual(sum(rl.allow("10.0.0.1", now=0) for _ in range(10)), 6)
        self.assertTrue(RateLimiter(0.2).allow("10.0.0.1", now=0))

    def test_clients_independent(self):
        rl = RateLimiter(1, burst=1)
        self.assertTrue(rl.allow("10.0.0.1", now=0))
        self.assertFalse(rl.allow("10.0.0.1", now=0))
        self.assertTrue(rl.allow("10.0.0.2", now=0))

    def test_burst_cap(self):
        rl = RateLimiter(2, burst=4)
        rl.allow("10.0.0.1", now=0)
        self.assertEqual(sum(rl.allow("10.0.0.1", now=1000) for _ in range(10)), 4)

    def test_loopback_never_limited(self):
        rl = RateLimiter(1, burst=1)
        for ip in ("127.0.0.1", "127.5.5.5", "::1"):
            self.assertTrue(all(rl.allow(ip, now=0) for _ in range(20)))
        self.assertEqual(rl.dropped, 0)
        self.assertEqual(len(rl), 0)

    def test_unlimited(self):
        for rate in (0, None):
            rl = RateLimiter(rate)
            self.assertTrue(all(rl.allow("10.0.0.1", now=0) for _ in range(1000)))
            self.assertEqual(rl.dropped, 0)

    def test_invalid_ip_allowed(self):
        rl = RateLimiter(1, burst=1)
        self.assertTrue(rl.allow("pas une ip", now=0))
        self.assertTrue(rl.allow("pas une ip", now=0))

    def test_max_clients_eviction(self):
        rl = RateLimiter(1, burst=1, max_clients=2)
        rl.allow("10.0.0.1", now=0)
        rl.allow("10.0.0.2", now=0)
        rl.allow("10.0.0.1", now=0)  # 10.0.0.2 devient le moins récent
        rl.allow("10.0.0.3", now=0)
        self.assertEqual(len(rl), 2)
        # 10.0.0.2 oublié : repart avec un seau plein ; 10.0.0.3 est vide
        self.assertTrue(rl.allow("10.0.0.2", now=0))
        self.assertFalse(rl.allow("10.0.0.3", now=0))

    def test_mapped_same_client(self):
        rl = RateLimiter(1, burst=1)
        self.assertTrue(rl.allow("10.0.0.1", now=0))
        self.assertFalse(rl.allow("::ffff:10.0.0.1", now=0))


if __name__ == "__main__":
    unittest.main()
