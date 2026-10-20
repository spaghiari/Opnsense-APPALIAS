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

"""Contrôle d'accès et débit par client du relais DNS (0.9).

Module pur : aucune E/S. ClientAcl décide si une adresse source peut
utiliser le relais (réseaux privés et bouclage par défaut). RateLimiter
applique un seau à jetons par adresse, avec un suivi borné à max_clients
clients (le moins récemment vu est oublié). Le bouclage n'est jamais limité,
ni les adresses de l'ensemble exempt (adresses d'écoute du relais : Unbound
peut l'interroger depuis l'adresse du pare-feu).
"""

import ipaddress
import time
from collections import OrderedDict

DEFAULT_CLIENT_NETWORKS = ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12",
                           "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
                           "fc00::/7", "fe80::/10")


def _to_address(ip):
    """Adresse ipaddress (IPv4 mappée ramenée en IPv4), ou None si invalide."""
    try:
        if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            ip = ipaddress.ip_address(str(ip).strip().split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


class ClientAcl:
    def __init__(self, networks=None):
        if networks:
            self.networks = [ipaddress.ip_network(n, strict=False) if isinstance(n, str) else n
                             for n in networks]
        else:
            self.networks = [ipaddress.ip_network(n) for n in DEFAULT_CLIENT_NETWORKS]

    def allowed(self, ip):
        addr = _to_address(ip)
        if addr is None:
            return False
        return any(addr.version == net.version and addr in net for net in self.networks)


class RateLimiter:
    def __init__(self, rate, burst=None, max_clients=4096, clock=time.monotonic, exempt=()):
        self.rate = float(rate) if rate else 0.0
        self.exempt = set()   # adresses jamais limitées (en plus du bouclage)
        self.set_exempt(exempt)
        self.burst = float(burst) if burst else max(2 * self.rate, 1.0)
        self.max_clients = max(int(max_clients), 1)
        self._clock = clock
        self._clients = OrderedDict()  # adresse -> [jetons, dernier instant]
        self.dropped = 0

    def __len__(self):
        return len(self._clients)

    def set_exempt(self, addresses):
        """Remplace l'ensemble des adresses jamais limitées (invalides ignorées)."""
        out = set()
        for item in addresses or ():
            addr = _to_address(item)
            if addr is not None:
                out.add(addr)
        self.exempt = out

    def allow(self, ip, now=None):
        if self.rate <= 0:
            return True
        addr = _to_address(ip)
        if addr is None or addr.is_loopback or addr in self.exempt:
            return True
        if now is None:
            now = self._clock()
        slot = self._clients.get(addr)
        if slot is None:
            slot = [self.burst, now]
            self._clients[addr] = slot
            while len(self._clients) > self.max_clients:
                self._clients.popitem(last=False)
        else:
            self._clients.move_to_end(addr)
            slot[0] = min(self.burst, slot[0] + max(now - slot[1], 0.0) * self.rate)
            slot[1] = now
        if slot[0] >= 1.0:
            slot[0] -= 1.0
            return True
        self.dropped += 1
        return False
