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

"""Cache de réponses DNS du relais (0.9).

Module pur : aucune E/S, aucun journal sur le chemin chaud, horloge injectée.
La clé est (nom minuscule ASCII, type, classe, bit DO) de la requête. Une
requête RD=0 ou CD=1 n'a pas de clé (dnswire.cache_key) : jamais servie
depuis le cache ni mise en cache. Une réponse n'est mise en cache que si sa
question (nom, type, classe) est celle de la requête. Une entrée garde la
réponse telle que reçue du serveur amont ; à la lecture, elle est réécrite
pour le client (ID, question à la casse du client, TTL décrémentés, OPT
retiré si le client n'en a pas) par dnswire.rewrite_for_client.

Bornes : max_entries entrées et max_bytes octets au total (éviction des
expirées puis des plus anciennes, par ordre d'insertion), TTL plafonné à
max_ttl, réponses de MAX_ENTRY_BYTES octets au plus. Le cache n'est pas
protégé par un verrou : l'appelant le sert depuis un seul thread ou ajoute
le sien.
"""

import time
from collections import OrderedDict

from . import dnswire

MAX_ENTRY_BYTES = 4096            # au-delà, une réponse n'est pas mise en cache
MAX_BYTES = 16 * 1024 * 1024      # budget mémoire global (taille des réponses)


class Cache:
    def __init__(self, max_entries=10000, max_ttl=86400, clock=time.time,
                 max_bytes=MAX_BYTES, max_entry_bytes=MAX_ENTRY_BYTES):
        self.max_entries = max_entries
        self.max_ttl = max_ttl  # modifiable à chaud
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self._clock = clock
        self._entries = OrderedDict()  # clé -> (réponse, stored_at, expiry, longueur de question)
        self.bytes = 0
        self.hits = 0
        self.misses = 0

    def __len__(self):
        return len(self._entries)

    def clear(self):
        self._entries.clear()
        self.bytes = 0

    def _drop(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self.bytes -= len(entry[0])

    def get(self, query, now=None):
        """Réponse à rendre pour query, ou None (miss)."""
        if now is None:
            now = self._clock()
        key = dnswire.cache_key(query)
        entry = self._entries.get(key) if key is not None else None
        if entry is None:
            self.misses += 1
            return None
        stored, stored_at, expiry, qlen = entry
        if now >= expiry:
            self._drop(key)
            self.misses += 1
            return None
        span = dnswire.question_span(query)
        if span is None or span[1] - span[0] != qlen:
            self.misses += 1
            return None
        self.hits += 1
        return dnswire.rewrite_for_client(query, stored, int(now - stored_at))

    def put(self, query, response, now=None):
        """Mémorise response pour query ; False si elle n'est pas cachable."""
        if self.max_entries <= 0:
            return False
        key = dnswire.cache_key(query)
        if key is None:
            return False
        if dnswire.question_tuple(response) != key[:3]:
            return False  # réponse à une autre question (nom, type ou classe)
        ttl = dnswire.response_ttl(response)
        if ttl is None:
            return False
        ttl = min(ttl, self.max_ttl)
        if ttl <= 0:
            return False
        response = bytes(response)
        if len(response) > min(self.max_entry_bytes, dnswire.MAX_MESSAGE):
            return False
        if len(response) > self.max_bytes:
            return False
        span = dnswire.question_span(response)
        if span is None:
            return False
        if now is None:
            now = self._clock()
        self._drop(key)
        if (len(self._entries) >= self.max_entries
                or self.bytes + len(response) > self.max_bytes):
            self._make_room(now, len(response))
        self._entries[key] = (response, now, now + ttl, span[1] - span[0])
        self.bytes += len(response)
        return True

    def _make_room(self, now, size):
        """Expirées d'abord, puis les plus anciennes, jusqu'à tenir en entrées et en octets."""
        for key in [k for k, e in self._entries.items() if now >= e[2]]:
            self._drop(key)
        while self._entries and (len(self._entries) >= self.max_entries
                                 or self.bytes + size > self.max_bytes):
            _key, entry = self._entries.popitem(last=False)
            self.bytes -= len(entry[0])
