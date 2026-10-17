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

"""Client DNS over TLS (RFC 7858) du relais.

Transport : TCP + TLS, chaque message précédé de sa longueur sur 2 octets
(RFC 1035 §4.2.2, RFC 7858 §3.3). La requête du client est transmise telle
quelle, ID compris.

Choix : une requête à la fois par connexion, avec un pool borné de
connexions persistantes par amont (POOL_SIZE), plutôt que le pipelining.
Sans démultiplexage par ID, une réponse ne peut appartenir qu'à la requête
en cours sur sa connexion : pas de table des requêtes en vol, pas de réponse
tardive à écarter, pas de collision d'ID entre clients. Une connexion dont
l'échange est annulé (délai dépassé) est simplement fermée. La poignée de
main TLS n'est payée qu'à l'ouverture d'une connexion ; au-delà de POOL_SIZE
requêtes simultanées vers un amont, les suivantes attendent une connexion
libre, dans la limite du délai de l'appelant.

Connexions : réutilisées tant qu'elles ont servi il y a moins de
IDLE_TIMEOUT secondes ; au-delà, ou si l'amont les a fermées, elles sont
fermées et remplacées. Une connexion réutilisée qui échoue (fermée par
l'amont pendant l'inactivité) est remplacée une fois par une connexion
neuve ; l'échec d'une connexion neuve fait échouer l'échange.

Sécurité : ssl.create_default_context(), check_hostname et CERT_REQUIRED,
TLS 1.2 au minimum, CA du système OPNsense (CA_BUNDLE, que le core donne
aussi à Unbound en tls-cert-bundle), sinon CA par défaut de Python. Aucun
repli en clair : un échec TLS est une erreur d'échange, l'appelant passe à
l'amont suivant.
"""

import asyncio
import logging
import os
import ssl
import struct
import time

CA_BUNDLE = "/usr/local/etc/ssl/cert.pem"
POOL_SIZE = 8
IDLE_TIMEOUT = 30.0
LOG_INTERVAL = 60.0
LOG_KEYS_MAX = 256

log = logging.getLogger("appalias")


class DotError(Exception):
    """Réponse DoT illisible (longueur nulle)."""


def make_context(cafile=CA_BUNDLE):
    """Contexte TLS client vérifiant certificat et nom ; CA de cafile s'il est lisible."""
    ctx = None
    if cafile and os.path.isfile(cafile):
        try:
            ctx = ssl.create_default_context(cafile=cafile)
        except (OSError, ssl.SSLError) as exc:
            log.warning("magasin de CA %s illisible, CA par défaut de Python : %s", cafile, exc)
    elif cafile:
        log.info("magasin de CA %s absent, CA par défaut de Python", cafile)
    if ctx is None:
        ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def failure_reason(exc):
    """Raison lisible d'un échec d'échange DoT, pour le journal."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "certificat refusé : %s" % (exc.verify_message or exc.reason or exc)
    if isinstance(exc, ssl.SSLError):
        return "erreur TLS : %s" % (exc.reason or exc)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "délai dépassé"
    if isinstance(exc, EOFError):  # asyncio.IncompleteReadError
        return "connexion fermée par l'amont"
    if isinstance(exc, DotError):
        return str(exc)
    if isinstance(exc, OSError):
        return "connexion impossible : %s" % (exc.strerror or exc)
    return "erreur : %r" % (exc,)


class LogLimiter:
    """Un même message (clé) au plus une fois toutes les interval secondes."""

    def __init__(self, interval=LOG_INTERVAL, clock=time.monotonic):
        self.interval = interval
        self.clock = clock
        self._last = {}
        self._muted = {}

    def check(self, key):
        """(émettre ?, nombre d'occurrences tues depuis le dernier message émis)."""
        now = self.clock()
        last = self._last.get(key)
        if last is not None and now - last < self.interval:
            self._muted[key] = self._muted.get(key, 0) + 1
            return False, 0
        if key not in self._last and len(self._last) >= LOG_KEYS_MAX:
            self._last.clear()
            self._muted.clear()
        self._last[key] = now
        return True, self._muted.pop(key, 0)


class _Conn:
    __slots__ = ("reader", "writer", "last_used")

    def __init__(self, reader, writer, last_used):
        self.reader = reader
        self.writer = writer
        self.last_used = last_used

    def usable(self, now, idle_timeout):
        return (now - self.last_used < idle_timeout and not self.reader.at_eof()
                and not self.writer.is_closing())

    def close(self):
        try:
            self.writer.close()
        except Exception:
            pass


class DotPool:
    """Connexions DoT persistantes vers un amont Upstream(host, port, tls_name)."""

    def __init__(self, upstream, context, size=POOL_SIZE, idle_timeout=IDLE_TIMEOUT,
                 clock=time.monotonic):
        self.upstream = upstream
        self.context = context
        self.size = size
        self.idle_timeout = idle_timeout
        self.clock = clock
        self.handshakes = 0      # poignées de main TLS réussies
        self._idle = []
        self._sem = None         # créé dans la boucle asyncio, au premier échange
        self._closed = False

    async def _connect(self):
        host, port, name = self.upstream
        reader, writer = await asyncio.open_connection(host, port, ssl=self.context,
                                                       server_hostname=name)
        self.handshakes += 1
        return _Conn(reader, writer, self.clock())

    def _take_idle(self):
        now = self.clock()
        while self._idle:
            conn = self._idle.pop()   # la plus récente d'abord
            if conn.usable(now, self.idle_timeout):
                return conn
            conn.close()
        return None

    @staticmethod
    async def _roundtrip(conn, query):
        conn.writer.write(struct.pack("!H", len(query)) + query)
        await conn.writer.drain()
        (size,) = struct.unpack("!H", await conn.reader.readexactly(2))
        if size == 0:
            raise DotError("réponse vide")
        return await conn.reader.readexactly(size)

    def _release(self, conn, query, resp):
        if self._closed or resp[:2] != query[:2]:
            conn.close()          # pool fermé, ou connexion désynchronisée
            return
        conn.last_used = self.clock()
        self._idle.append(conn)

    async def exchange(self, query):
        """Envoie query et renvoie la réponse brute.

        Lève OSError (dont ssl.SSLError), EOFError (IncompleteReadError) ou
        DotError ; une annulation (délai de l'appelant) ferme la connexion.
        """
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.size)
        async with self._sem:
            conn = self._take_idle()
            if conn is not None:
                try:
                    resp = await self._roundtrip(conn, query)
                except (OSError, EOFError):
                    conn.close()  # fermée par l'amont : nouvel essai sur une connexion neuve
                except BaseException:
                    conn.close()
                    raise
                else:
                    self._release(conn, query, resp)
                    return resp
            conn = await self._connect()
            try:
                resp = await self._roundtrip(conn, query)
            except BaseException:
                conn.close()
                raise
            self._release(conn, query, resp)
            return resp

    def prune(self):
        """Ferme les connexions inactives depuis IDLE_TIMEOUT ou fermées par l'amont."""
        now = self.clock()
        keep = []
        for conn in self._idle:
            if conn.usable(now, self.idle_timeout):
                keep.append(conn)
            else:
                conn.close()
        self._idle = keep

    @property
    def idle_count(self):
        return len(self._idle)

    def close(self):
        """Ferme les connexions libres ; celles en cours le sont à la fin de leur échange."""
        self._closed = True
        for conn in self._idle:
            conn.close()
        self._idle = []
