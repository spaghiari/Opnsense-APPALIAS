#!/usr/local/bin/python3
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

"""appaliasd : relais DNS d'os-appalias.

Chaque requête reçue (UDP ou TCP) est transmise telle quelle à l'amont
configuré. Dans la réponse, les adresses A/AAAA des noms qui correspondent
à une application sont ajoutées à la table pf de l'alias External de cette
application (APP_<NOM>), **avant** que la réponse soit rendue au client.

Amont : DNS clair (UDP pour un client UDP, TCP pour un client TCP) ou DNS
over TLS (lib/dot.py, connexions persistantes). En DoT, jamais de repli en
clair : échec TLS = amont suivant, puis SERVFAIL ; une réponse plus grande
que la taille UDP du client est rendue tronquée (TC=1) pour qu'il réessaie
en TCP.

Concurrence : tous les appels pfctl d'une table (add, delete, replace,
flush) sont sérialisés par un verrou asyncio propre à la table, et l'état
n'est modifié qu'après le succès de l'appel correspondant.

Mesures : temps de l'amont (autour de l'échange) et de pf (autour de
_add), en mémoire (lib/metrics.py) ; résumé écrit toutes les
METRICS_INTERVAL secondes dans metrics.json, jamais sur le chemin de réponse.

Découverte (lib/discovery.py, désactivée par défaut) : sur le chemin de
réponse, un simple append dans une file bornée ; agrégation, persistance
(discovery.json) et mode Learn dans des tâches périodiques. Rien n'est
jamais ajouté à une application par la découverte.

Résolution proactive (lib/proactive.py) : une tâche de fond résout elle-même,
à intervalle régulier, les noms des applications activées (au plus
PROACTIVE_MAX_CONCURRENT sondes simultanées, PROACTIVE_RATE par seconde). Les
réponses passent par process_response(observe=False, hit=False) : jamais de
découverte, jamais de « dernier usage », jamais de latence client.

Version : le démon écrit la version de son code, son PID et son heure de
démarrage dans metrics.json dès le démarrage (status.py la compare à la
version installée).

Signaux : SIGHUP = relecture de config.json et remplacement des tables ;
SIGUSR1 = commande de découverte (discovery.ctl.json) ;
SIGTERM/SIGINT = sauvegarde de l'état puis sortie.

Codes de sortie : 0 arrêt normal, 1 erreur d'exécution, 78 (EX_CONFIG)
configuration absente ou invalide. daemon(8) -R 10 relance le démon.
"""

import argparse
import asyncio
import ipaddress
import logging
import os
import random
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import config as cfgmod  # noqa: E402
from lib import discovery as discmod  # noqa: E402
from lib import dnswire  # noqa: E402
from lib import dot  # noqa: E402
from lib import metrics  # noqa: E402
from lib import proactive  # noqa: E402
from lib import shared  # noqa: E402
from lib.matcher import Matcher  # noqa: E402
from lib.pftable import PfError, PfTable, normalize_entry  # noqa: E402
from lib.state import State, compute_expiry  # noqa: E402
from lib.version import VERSION  # noqa: E402

log = logging.getLogger("appalias")

UDP_TIMEOUT = 2.0
TCP_TIMEOUT = 5.0
TCP_IDLE_TIMEOUT = 10.0
DOT_TIMEOUT = 3.0           # par amont, poignée de main TLS comprise
DOT_PRUNE_INTERVAL = 10.0
METRICS_INTERVAL = 15.0
SWEEP_INTERVAL = 30.0
SAVE_INTERVAL = 60.0
PF_COOLDOWN = 30.0
MAX_INFLIGHT_QUERIES = 512
MAX_TCP_CLIENTS = 128
PROACTIVE_MAX_CONCURRENT = 2     # sondes proactives simultanées au plus
PROACTIVE_RATE = 10.0            # sondes lancées par seconde au plus
PROACTIVE_SLEEP_MIN = 1.0        # sommeil de la boucle (s), hors arriéré
PROACTIVE_SLEEP_MAX = 30.0
PROACTIVE_DEFER_JITTER = 5.0     # gigue ajoutée à un report (cooldown pfctl), en s
PROACTIVE_RETRY_DELAY = 1.0      # report après un rechargement survenu pendant la sonde

_UNCHECKED = object()  # process_response sans contrôle de la question (appel direct)

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_CONFIG = 78


# --- journalisation -------------------------------------------------------
class SyslogHandler(logging.Handler):
    """Envoie les messages à syslog (ident « appalias », facility daemon)."""

    def __init__(self, syslog_mod):
        super().__init__()
        self.syslog = syslog_mod
        self.syslog.openlog("appalias", self.syslog.LOG_PID, self.syslog.LOG_DAEMON)
        self.prio = {
            logging.DEBUG: self.syslog.LOG_DEBUG,
            logging.INFO: self.syslog.LOG_INFO,
            logging.WARNING: self.syslog.LOG_WARNING,
            logging.ERROR: self.syslog.LOG_ERR,
            logging.CRITICAL: self.syslog.LOG_CRIT,
        }

    def emit(self, record):
        try:
            self.syslog.syslog(self.prio.get(record.levelno, self.syslog.LOG_INFO),
                               self.format(record))
        except Exception:
            self.handleError(record)


def setup_logging(level=logging.INFO, use_syslog=True):
    logger = logging.getLogger("appalias")
    logger.handlers.clear()
    handler = None
    if use_syslog:
        try:
            import syslog  # absent sous Windows
            handler = SyslogHandler(syslog)
            handler.setFormatter(logging.Formatter("%(message)s"))
        except ImportError:
            handler = None
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("appalias: %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


# --- protocoles asyncio ---------------------------------------------------
class _UdpServer(asyncio.DatagramProtocol):
    def __init__(self, relay):
        self.relay = relay
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.relay.spawn(self._handle(data, addr))

    async def _handle(self, data, addr):
        resp = await self.relay.handle_query(data, "udp")
        if resp is not None and self.transport is not None and not self.transport.is_closing():
            self.transport.sendto(resp, addr)

    def error_received(self, exc):
        log.debug("erreur UDP côté client : %s", exc)


class _UdpClient(asyncio.DatagramProtocol):
    def __init__(self, future, qid):
        self.future = future
        self.qid = qid

    def datagram_received(self, data, addr):
        if data[:2] == self.qid and not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc):
        if not self.future.done():
            self.future.set_exception(exc)

    def connection_lost(self, exc):
        if not self.future.done():
            self.future.set_exception(exc or ConnectionError("socket fermée"))


# --- relais ---------------------------------------------------------------
class Relay:
    def __init__(self, cfg, pf, state_path=cfgmod.STATE_PATH, config_path=cfgmod.CONFIG_PATH,
                 clock=time.time, tls_cafile=dot.CA_BUNDLE):
        self.cfg = cfg
        self.pf = pf
        self.state_path = state_path
        self.config_path = config_path
        self.clock = clock
        self.state = State()
        self.matcher = Matcher({})
        self.apps = {}
        self.udp_timeout = UDP_TIMEOUT
        self.tcp_timeout = TCP_TIMEOUT
        self.dot_timeout = DOT_TIMEOUT
        self.tls_cafile = tls_cafile
        self._tls_context = None
        self._pools = {}         # Upstream -> dot.DotPool
        self._dot_log = dot.LogLimiter()
        self.metrics = metrics.Metrics()
        self.metrics_path = metrics.path_for(state_path)
        self._metrics_saved = -1
        self.sweep_interval = SWEEP_INTERVAL
        self.save_interval = SAVE_INTERVAL
        self.pf_cooldown = PF_COOLDOWN
        self.max_tcp_clients = MAX_TCP_CLIENTS
        self.loop = None
        self.ready = None
        self._stop = None
        self._tasks = set()
        self._inflight = {}      # (table, ip) -> (future, expiry)
        self._locks = {}         # table -> asyncio.Lock
        self._cooldown = {}      # table -> fin de la pause après un échec pfctl
        self._cap_logged = set()
        self._tcp_clients = 0
        self._udp_transport = None
        self._tcp_server = None
        self._sem = None
        self.started = time.time()
        self.discovery = None    # discmod.Engine quand la découverte est activée
        self._observer = None    # son Observer, seul objet touché sur le chemin de réponse
        self.discovery_path = os.path.join(os.path.dirname(os.path.abspath(state_path)),
                                           os.path.basename(discmod.DISCOVERY_PATH))
        self.ctl_path = discmod.ctl_path_for(self.discovery_path)
        self._discovery_purged = 0.0
        self._config_gen = 0     # incrémenté à chaque configuration appliquée
        self.sched = None        # proactive.Scheduler
        self._proactive_targets = {}   # (nom, qtype) -> {tables}
        self._proactive_wake = None    # asyncio.Event, créé dans la boucle
        self._proactive_active = 0     # sondes en cours
        self._proactive_task = None
        self._proactive_rng = random.Random()
        self._apply_config(cfg)

    # -- configuration --
    def _apply_config(self, cfg):
        self._config_gen += 1
        self.cfg = cfg
        self.apps = {a.table: a for a in cfg.apps}
        self.matcher = Matcher({a.table: a.domains for a in cfg.apps if a.enabled},
                               platforms=shared.load(extra=cfg.shared_platforms_extra))
        self._update_pools(cfg)
        self.metrics.retain({self._upstream_key(u) for u in cfg.upstreams})
        self._update_discovery(cfg)
        self._update_proactive(cfg)

    def _update_proactive(self, cfg):
        """Reconstruit les cibles proactives ; Scheduler recréé si les intervalles changent."""
        self._proactive_targets = (proactive.targets(cfg, self.matcher, self.matcher.platforms)
                                   if cfg.proactive_enabled else {})
        sched = self.sched
        if (sched is None or sched.interval_min != cfg.proactive_interval_min
                or sched.interval_max != max(cfg.proactive_interval_max,
                                             cfg.proactive_interval_min)):
            sched = self.sched = proactive.Scheduler(
                self.clock, cfg.proactive_interval_min, cfg.proactive_interval_max,
                self._proactive_rng)
        sched.load(self._proactive_targets)
        self._proactive_notify()

    def _proactive_notify(self):
        if self._proactive_wake is not None:
            self._proactive_wake.set()

    def _update_discovery(self, cfg):
        """Active, règle ou arrête la découverte selon la configuration.

        Désactivée : mémoire vidée et discovery.json supprimé (vie privée).
        Activée au démarrage ou au rechargement : fichier relu (purge de la
        rétention comprise) quand le moteur n'existait pas encore.
        """
        if not cfg.discovery_enabled:
            if self.discovery is not None:
                log.info("découverte désactivée : données effacées")
            self._observer = None
            self.discovery = None
            try:
                discmod.remove_file(self.discovery_path)
                discmod.remove_file(self.ctl_path)
            except OSError as exc:
                log.warning("découverte : suppression de %s impossible : %s", self.discovery_path, exc)
            return
        ignored = discmod.Ignored(cfg.discovery_ignored)
        if self.discovery is None:
            engine = discmod.Engine(self.matcher.match_name, window=cfg.discovery_window,
                                    max_names=cfg.discovery_max_names,
                                    retention_days=cfg.discovery_retention_days,
                                    ignored=ignored, clock=self.clock, path=self.discovery_path)
            engine.load()
            self.discovery = engine
            self._discovery_purged = self.clock()
            log.info("découverte activée (%d noms relus)", len(engine.names))
        else:
            self.discovery.configure(cfg.discovery_window, cfg.discovery_max_names,
                                     cfg.discovery_retention_days, ignored, self.matcher.match_name)
        self._observer = self.discovery.observer

    @staticmethod
    def _upstream_key(upstream):
        return "%s@%d" % (upstream.host, upstream.port)

    def _measured(self, upstream, start, timing, measure=True):
        ms = (time.monotonic() - start) * 1000.0
        if measure:
            self.metrics.add_upstream(self._upstream_key(upstream), ms)
        if timing is not None:
            timing["upstream_ms"] = ms

    def _update_pools(self, cfg):
        """Un pool DoT par amont en « dot » ; ceux des amonts retirés sont fermés."""
        wanted = set(cfg.upstreams) if cfg.upstream_protocol == "dot" else set()
        for upstream in list(self._pools):
            if upstream not in wanted:
                self._pools.pop(upstream).close()
        if wanted and self._tls_context is None:
            self._tls_context = dot.make_context(self.tls_cafile)
        for upstream in wanted:
            if upstream not in self._pools:
                self._pools[upstream] = dot.DotPool(upstream, self._tls_context)

    def prune_pools(self):
        for pool in list(self._pools.values()):
            pool.prune()

    def close_pools(self):
        for pool in self._pools.values():
            pool.close()
        self._pools = {}

    def _dns_entry_ok(self, app, ip):
        return app.family_ok(ip) and not self.cfg.excluded(ip) and not app.covered(ip)

    # -- pfctl sérialisé --
    def _lock(self, table):
        lock = self._locks.get(table)
        if lock is None:
            lock = self._locks[table] = asyncio.Lock()
        return lock

    def in_cooldown(self, table):
        return self._cooldown.get(table, 0.0) > self.clock()

    async def _pf(self, table, func, *args, force=False):
        """Appel pfctl dans l'executor. À appeler sous le verrou de la table.

        Renvoie True si l'appel a réussi, False s'il a échoué ou a été sauté
        (table en pause après un échec, sauf force=True).
        """
        if not force and self.in_cooldown(table):
            log.debug("%s : appel pfctl sauté (pause après échec)", table)
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, func, table, *args)
        except PfError as exc:
            now = self.clock()
            self.state.error(table, now, exc)
            self._cooldown[table] = now + self.pf_cooldown
            log.error("%s : échec de pfctl, table ignorée pendant %d s : %s",
                      table, int(self.pf_cooldown), exc)
            return False
        self._cooldown.pop(table, None)
        self.state.clear_error(table)
        return True

    async def restore_tables(self):
        """Remplace chaque table par IP DNS valides + CIDR manuels ; vide les apps désactivées.

        Chaque replace est calculé et exécuté sous le verrou de la table, à
        partir de l'état et des IP en cours d'ajout.
        """
        self.state.prune_tables(set(self.apps))
        for table, app in list(self.apps.items()):
            async with self._lock(table):
                now = self.clock()
                if not app.enabled:
                    if await self._pf(table, self.pf.flush, force=True):
                        self.state.drop_table(table)
                        log.info("%s : application désactivée, table vidée", table)
                    continue
                for ipstr in self.state.expired(now).get(table, []):
                    self.state.remove(table, ipstr)
                candidates = set(self.state.valid(table, now))
                candidates.update(ip for (t, ip) in self._inflight if t == table)
                kept = []
                for ipstr in sorted(candidates):
                    try:
                        ip = ipaddress.ip_address(ipstr)
                    except ValueError:
                        self.state.remove(table, ipstr)
                        continue
                    if self._dns_entry_ok(app, ip):
                        kept.append(ipstr)
                    else:
                        self.state.remove(table, ipstr)
                manual = [normalize_entry(n) for n in app.networks]
                if await self._pf(table, self.pf.replace, manual + kept, force=True):
                    log.info("%s : table remplacée (%d IP DNS, %d CIDR manuels)",
                             table, len(kept), len(manual))

    # -- tâches --
    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def handle_query(self, query, proto):
        """Transmet la requête, traite la réponse, la renvoie (ou None)."""
        if len(query) < 12:
            return None
        if self._sem is None:
            self._sem = asyncio.Semaphore(MAX_INFLIGHT_QUERIES)
        if self._sem.locked():
            log.warning("trop de requêtes simultanées, requête ignorée")
            return None
        async with self._sem:
            stream = self.cfg.upstream_protocol == "dot"
            timing = {}
            resp = await self.forward(query, proto, timing)
            if resp is None:
                return dnswire.make_servfail(query)
            try:
                await self.process_response(resp, expect=dnswire.question_key(query),
                                            upstream_ms=timing.get("upstream_ms"))
            except Exception as exc:  # la réponse part quoi qu'il arrive
                log.error("erreur interne au traitement d'une réponse : %s", exc)
            if proto == "udp" and stream:
                # Réponse lue sur un flux : bornée à la taille UDP du client (TC=1 sinon).
                return dnswire.fit_udp(query, resp)
            return resp

    async def forward(self, query, proto, timing=None, measure=True):
        """Réponse du premier amont qui répond, ou None ; timing["upstream_ms"] si fourni.

        measure=False : la latence amont n'entre pas dans les métriques (requêtes
        qui ne viennent pas d'un client) ; timing est quand même renseigné.
        """
        if self.cfg.upstream_protocol == "dot":
            return await self._forward_dot(query, timing, measure)
        for upstream in self.cfg.upstreams:
            host, port, _name = upstream
            start = time.monotonic()
            try:
                if proto == "tcp":
                    resp = await asyncio.wait_for(self._tcp_exchange(query, host, port),
                                                  self.tcp_timeout)
                else:
                    resp = await asyncio.wait_for(self._udp_exchange(query, host, port),
                                                  self.udp_timeout)
                self._measured(upstream, start, timing, measure)
                return resp
            except (asyncio.TimeoutError, OSError, ConnectionError,
                    asyncio.IncompleteReadError) as exc:
                log.debug("amont %s:%s (%s) sans réponse : %r", host, port, proto, exc)
                continue
        self._warn_limited(("plain", "all"), "aucun amont n'a répondu (%s)" % proto)
        return None

    async def _forward_dot(self, query, timing=None, measure=True):
        """Amonts DoT dans l'ordre ; jamais de repli en clair."""
        for upstream in self.cfg.upstreams:
            pool = self._pools.get(upstream)
            if pool is None:
                continue
            start = time.monotonic()
            try:
                resp = await asyncio.wait_for(pool.exchange(query), self.dot_timeout)
                self._measured(upstream, start, timing, measure)
                return resp
            except Exception as exc:  # TLS, délai, connexion : amont suivant
                self._dot_failed(upstream, exc)
        self._warn_limited(("dot", "all"), "aucun amont DNS over TLS n'a répondu : SERVFAIL")
        return None

    def _dot_failed(self, upstream, exc):
        reason = dot.failure_reason(exc)
        self._warn_limited((upstream, reason), "amont DNS over TLS %s port %d (%s) : %s"
                           % (upstream.host, upstream.port, upstream.tls_name, reason))

    def _warn_limited(self, key, message):
        emit, muted = self._dot_log.check(key)
        if not emit:
            log.debug("%s", message)
            return
        if muted:
            message += " (%d fois de plus depuis le message précédent)" % muted
        log.warning("%s", message)

    async def _udp_exchange(self, query, host, port):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpClient(fut, query[:2]), remote_addr=(host, port))
        try:
            transport.sendto(query)
            return await fut
        finally:
            transport.close()

    async def _tcp_exchange(self, query, host, port):
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(struct.pack("!H", len(query)) + query)
            await writer.drain()
            hdr = await reader.readexactly(2)
            (size,) = struct.unpack("!H", hdr)
            return await reader.readexactly(size)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def _tcp_client(self, reader, writer):
        if self._tcp_clients >= self.max_tcp_clients:
            log.debug("trop de connexions TCP simultanées, connexion fermée")
            writer.close()
            return
        self._tcp_clients += 1
        try:
            while True:
                hdr = await asyncio.wait_for(reader.readexactly(2), TCP_IDLE_TIMEOUT)
                (size,) = struct.unpack("!H", hdr)
                if size < 12:
                    break
                query = await asyncio.wait_for(reader.readexactly(size), TCP_IDLE_TIMEOUT)
                resp = await self.handle_query(query, "tcp")
                if resp is None:
                    break
                writer.write(struct.pack("!H", len(resp)) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, OSError, ConnectionError):
            pass
        finally:
            self._tcp_clients -= 1
            writer.close()

    # -- cœur : ajout des IP --
    async def process_response(self, raw, expect=_UNCHECKED, upstream_ms=None, *,
                               observe=True, hit=True, source="relay"):
        """Ajoute les IP de la réponse aux tables concernées.

        expect = (id, qname) de la requête (dnswire.question_key) : si la
        réponse ne correspond pas, rien n'est ajouté (l'appelant transmet la
        réponse quand même). expect=None (question de la requête illisible) :
        rien n'est ajouté. Sans expect : pas de contrôle. upstream_ms : temps
        de l'amont, repris dans la ligne de journal des ajouts.

        Options (défauts = comportement du relais) : observe=False n'alimente
        pas la découverte ; hit=False ne met pas à jour le « dernier usage »
        des tables ; source est une simple étiquette pour les journaux
        ("relay" ou "proactive"), une valeur inconnue ne change rien.
        """
        if expect is None:
            log.debug("question de la requête illisible : réponse transmise sans traitement")
            return
        resp = dnswire.parse_response(raw)
        if resp is None or not resp.answers:
            return
        if expect is not _UNCHECKED and (resp.id, resp.qname) != tuple(expect):
            log.debug("réponse ignorée : id/qname (%s, %s) différents de la requête (%s, %s)",
                      resp.id, resp.qname, expect[0], expect[1])
            return
        chain = dnswire.cname_chain(resp)
        names = chain if self.cfg.follow_cname else [resp.qname]
        tables = self.matcher.match(names)
        observer = self._observer
        if observer is not None and observe:
            # Découverte : un append, rien d'autre (ni calcul, ni disque, ni attente).
            observer.observe(resp.qname, chain, resp.answers, self.clock(), tables)
        if not tables:
            return
        addrs = dnswire.addresses(resp, chain)
        now = self.clock()
        jobs = []
        waits = []
        for table in sorted(tables):
            app = self.apps.get(table)
            if app is None or not app.enabled:
                continue
            if hit:
                self.state.hit(table, now)
            new = []
            for ip, ttl in addrs:
                if not self._dns_entry_ok(app, ip):
                    continue
                ipstr = str(ip)
                expiry = compute_expiry(now, ttl, self.cfg.ttl_min, self.cfg.ttl_margin)
                key = (table, ipstr)
                if key in self._inflight:
                    waits.append(self._inflight[key][0])
                    continue
                if self.state.is_known(table, ipstr, now):
                    self.state.set(table, ipstr, expiry)
                    continue
                new.append((ipstr, expiry))
            if not new or self.in_cooldown(table):
                continue
            room = self.cfg.max_entries_per_app - self.state.count(table) - len(app.networks)
            if len(new) > room:
                if table not in self._cap_logged:
                    log.warning("%s : plafond de %d entrées atteint, %d IP non ajoutées",
                                table, self.cfg.max_entries_per_app, len(new) - max(room, 0))
                    self._cap_logged.add(table)
                new = new[:max(room, 0)]
            else:
                self._cap_logged.discard(table)
            if not new:
                continue
            fut = asyncio.get_running_loop().create_future()
            for ipstr, expiry in new:
                self._inflight[(table, ipstr)] = (fut, expiry)
            jobs.append(self._add(table, new, fut, upstream_ms, self._config_gen, source))
        if jobs or waits:
            await asyncio.gather(*jobs, *waits, return_exceptions=True)

    async def _add(self, table, new, fut, upstream_ms=None, gen=None, source="relay"):
        start = time.monotonic()
        queued = [ip for ip, _ in new]   # liste d'origine : _inflight se nettoie sur celle-ci
        try:
            async with self._lock(table):
                # Configuration relue sous le verrou : un rechargement a pu désactiver
                # ou supprimer l'application, ou exclure des IP, pendant l'attente.
                app = self.apps.get(table)
                if app is None or not app.enabled:
                    log.debug("%s : ajout abandonné (application absente ou désactivée)", table)
                    return
                if gen is not None and gen != self._config_gen:
                    kept = []
                    for ipstr, expiry in new:
                        try:
                            ok = self._dns_entry_ok(app, ipaddress.ip_address(ipstr))
                        except ValueError:
                            ok = False
                        if ok:
                            kept.append((ipstr, expiry))
                    if len(kept) != len(new):
                        log.debug("%s : %d IP écartées par la nouvelle configuration",
                                  table, len(new) - len(kept))
                    new = kept
                    if not new:
                        return
                ips = [ip for ip, _ in new]
                if await self._pf(table, self.pf.add, ips):
                    pf_ms = (time.monotonic() - start) * 1000.0
                    self.metrics.add_pf(pf_ms)
                    for ipstr, expiry in new:
                        self.state.set(table, ipstr, expiry)
                    if source == "proactive":
                        self.metrics.add_proactive("added", len(ips))
                    log.info("%s : ajout de %d IP (amont %s, pf %d ms%s) : %s", table, len(ips),
                             "%d ms" % upstream_ms if upstream_ms is not None else "-",
                             pf_ms, ", source %s" % source if source != "relay" else "",
                             " ".join(ips))
        finally:
            for ipstr in queued:
                self._inflight.pop((table, ipstr), None)
            if not fut.done():
                fut.set_result(None)

    # -- résolution proactive --
    async def proactive_loop(self):
        """Tâche de fond : lance les sondes échues dans la limite du débit.

        Ne s'arrête jamais sur une exception (journalisée) ; seule l'annulation
        (arrêt du démon) la termine.
        """
        if self._proactive_wake is None:
            self._proactive_wake = asyncio.Event()
        while True:
            self._proactive_wake.clear()
            try:
                delay = await self.proactive_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warn_limited(("proactive", "loop"),
                                   "résolution proactive : erreur interne : %r" % (exc,))
                delay = PROACTIVE_SLEEP_MAX
            try:
                await asyncio.wait_for(self._proactive_wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    async def proactive_pass(self):
        """Une passe : lance les sondes échues ; renvoie la durée de sommeil voulue.

        Le sommeil est interrompu par un rechargement ou la fin d'une sonde.
        """
        sched = self.sched
        if not self.cfg.proactive_enabled or sched is None or not len(sched):
            return PROACTIVE_SLEEP_MAX
        limit = PROACTIVE_MAX_CONCURRENT - self._proactive_active
        if limit <= 0:
            return PROACTIVE_SLEEP_MAX      # réveil à la fin d'une sonde
        keys = sched.due(self.clock(), limit)
        for key in keys:
            self._proactive_active += 1
            self.spawn(self._probe(sched, key))
        if keys:
            # débit : n sondes lancées = n / PROACTIVE_RATE s avant la suivante
            await asyncio.sleep(len(keys) / PROACTIVE_RATE)
            return 0.0
        nxt = sched.next_due()
        if nxt is None:
            return PROACTIVE_SLEEP_MAX
        return min(max(nxt - self.clock(), PROACTIVE_SLEEP_MIN), PROACTIVE_SLEEP_MAX)

    async def _probe(self, sched, key):
        """Sonde d'une cible : replanifie toujours la cible, ne lève jamais (hors annulation)."""
        try:
            gen = self._config_gen
            try:
                outcome, ttl = await self._probe_resolve(sched, key, gen)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warn_limited(("proactive", key),
                                   "résolution proactive de %s impossible : %r" % (key[0], exc))
                self.metrics.add_proactive("fail")
                outcome, ttl = "fail", None
            if self.sched is not sched:
                return              # Scheduler recréé : la cible a été rechargée
            now = self.clock()
            if outcome == "stale":
                sched.defer(key, now + PROACTIVE_RETRY_DELAY)
            elif outcome != "deferred":
                sched.done(key, now, outcome, ttl)
        finally:
            self._proactive_active -= 1
            self._proactive_notify()

    async def _probe_resolve(self, sched, key, gen):
        """(outcome, ttl) d'une cible. Outcomes en plus de ceux du Scheduler : « stale »
        (configuration changée pendant la sonde, réponse jetée) et « deferred »
        (déjà reportée, tables en pause après un échec pfctl)."""
        name, qtype = key
        tables = [t for t in self._proactive_targets.get(key, ())
                  if t in self.apps and self.apps[t].enabled]
        if not tables:
            return "stale", None
        now = self.clock()
        ends = [self._cooldown.get(t, 0.0) for t in tables]
        if all(end > now for end in ends):
            sched.defer(key, min(ends) + self._proactive_rng.uniform(0, PROACTIVE_DEFER_JITTER))
            return "deferred", None
        try:
            query = dnswire.build_query(name, qtype)
        except ValueError:
            self.metrics.add_proactive("fail")
            return "fail", None
        expect = dnswire.question_key(query)
        self.metrics.add_proactive("sent")           # une requête réseau réellement émise
        raw = await self.forward(query, "udp", measure=False)
        resp = dnswire.parse_response(raw) if raw is not None else None
        if resp is not None and resp.truncated and self.cfg.upstream_protocol != "dot":
            self.metrics.add_proactive("sent")       # le rejeu TCP est une requête de plus
            raw = await self.forward(query, "tcp", measure=False)   # TC : rejeu en TCP
            resp = dnswire.parse_response(raw) if raw is not None else None
        if gen != self._config_gen:
            return "stale", None
        if resp is None or expect is None or (resp.id, resp.qname) != tuple(expect):
            self.metrics.add_proactive("fail")
            return "fail", None
        if resp.rcode == 3:
            self.metrics.add_proactive("empty")
            return "nxdomain", None
        if resp.rcode != 0:
            self.metrics.add_proactive("fail")
            return "fail", None
        addrs = dnswire.addresses(resp, dnswire.cname_chain(resp))
        if not addrs:
            self.metrics.add_proactive("empty")
            return "nodata", None
        await self.process_response(raw, expect, observe=False, hit=False, source="proactive")
        self.metrics.add_proactive("ok")
        return "ok", min(ttl for _ip, ttl in addrs)

    # -- maintenance --
    async def sweep(self):
        """Retire les IP expirées. L'état n'est modifié qu'après le succès du delete."""
        now = self.clock()
        for table in list(self.state.expired(now)):
            async with self._lock(table):
                expired = self.state.expired(now).get(table, [])
                app = self.apps.get(table)
                manual = set(normalize_entry(n) for n in app.networks) if app else set()
                todo = [ip for ip in expired
                        if ip not in manual and (table, ip) not in self._inflight]
                for ip in expired:
                    if ip in manual:
                        self.state.remove(table, ip)
                if not todo:
                    continue
                if await self._pf(table, self.pf.delete, todo):
                    entries = self.state.tables.get(table, {})
                    for ip in todo:
                        exp = entries.get(ip)
                        if exp is not None and exp <= now:
                            entries.pop(ip, None)
                    log.debug("%s : %d IP expirées retirées", table, len(todo))

    def save_state(self):
        try:
            self.state.save(self.state_path)
        except OSError as exc:
            log.error("sauvegarde de l'état impossible (%s) : %s", self.state_path, exc)

    def daemon_info(self):
        """Version du code en cours d'exécution, PID et démarrage (metrics.json)."""
        return {"version": VERSION, "pid": os.getpid(), "started": int(self.started)}

    def save_metrics(self, force=False):
        """Écrit metrics.json si une mesure a changé depuis la dernière écriture."""
        if self.metrics.version == self._metrics_saved and not force:
            return
        try:
            metrics.write(self.metrics_path, dict(self.metrics.to_dict(), time=int(time.time()),
                                                  daemon=self.daemon_info()))
            self._metrics_saved = self.metrics.version
        except OSError as exc:
            log.debug("écriture des mesures impossible (%s) : %s", self.metrics_path, exc)

    # -- découverte (jamais sur le chemin de réponse) --
    async def discovery_drain(self):
        engine = self.discovery
        if engine is None:
            return
        await engine.drain()
        now = self.clock()
        if now - self._discovery_purged >= discmod.PURGE_INTERVAL:
            self._discovery_purged = now
            removed = engine.purge_old(now)
            if removed:
                log.debug("découverte : %d noms retirés (rétention)", removed)

    async def discovery_save(self, force=False):
        """Écrit discovery.json (instantané pris dans la boucle, écriture dans l'executor)."""
        engine = self.discovery
        if engine is None or not (engine.dirty or force):
            return
        generation = engine.generation
        engine.dirty = False     # avant la copie : un changement pendant la copie reste à écrire
        # sérialisation par tranches dans la boucle (Engine.snapshot_json), écriture
        # (disque, fsync) dans l'executor
        snapshot = await engine.snapshot_json()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, discmod.write_text, self.discovery_path, snapshot)
        except OSError as exc:
            engine.dirty = True
            log.error("découverte : écriture de %s impossible : %s", self.discovery_path, exc)
            return
        if self.discovery is not engine or engine.generation != generation:
            # désactivée ou purgée pendant l'écriture : le fichier ne doit pas rester
            try:
                discmod.remove_file(self.discovery_path)
            except OSError:
                pass

    def _discovery_save_sync(self):
        engine = self.discovery
        if engine is None:
            return
        try:
            discmod.write_json(self.discovery_path, engine.to_dict())
        except OSError as exc:
            log.error("découverte : écriture de %s impossible : %s", self.discovery_path, exc)

    def request_ctl(self):
        self.spawn(self.discovery_ctl())

    async def discovery_ctl(self):
        """Commande SIGUSR1 : discovery.ctl.json lu, validé, puis supprimé."""
        try:
            with open(self.ctl_path, "r", encoding="utf-8") as fh:
                text = fh.read(discmod.MAX_CTL_BYTES + 1)
        except OSError as exc:
            log.warning("découverte : commande illisible (%s) : %s", self.ctl_path, exc)
            return
        finally:
            try:
                discmod.remove_file(self.ctl_path)
            except OSError:
                pass
        engine = self.discovery
        if engine is None:
            log.info("découverte désactivée : commande ignorée")
            return
        now = self.clock()
        try:
            cmd = discmod.parse_ctl(text, now)
        except ValueError as exc:
            log.warning("découverte : commande refusée : %s", exc)
            return
        if cmd["cmd"] == "purge":
            engine.clear()
            try:
                discmod.remove_file(self.discovery_path)
            except OSError as exc:
                log.error("découverte : suppression de %s impossible : %s", self.discovery_path, exc)
            log.info("découverte : données effacées à la demande")
            return
        if cmd["cmd"] == "learn":
            engine.start_learn(cmd["until"], cmd["app_uuid"], now)
            log.info("découverte : apprentissage pendant %d s", int(cmd["until"] - now))
        elif cmd["cmd"] == "stop_learn":
            engine.stop_learn("user", now)
            log.info("découverte : apprentissage arrêté")
        await engine.drain(now)
        await self.discovery_save(force=True)

    async def _periodic(self, interval, func):
        while True:
            await asyncio.sleep(interval)
            try:
                res = func()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                log.error("tâche périodique en erreur : %s", exc)

    # -- écoute --
    async def open_listeners(self):
        loop = asyncio.get_running_loop()
        addr, port = self.cfg.listen_address, self.cfg.listen_port
        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpServer(self), local_addr=(addr, port))
        self._tcp_server = await asyncio.start_server(self._tcp_client, addr, port)
        log.info("écoute sur %s port %d (UDP et TCP)", addr, port)

    async def close_listeners(self):
        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None
        if self._tcp_server is not None:
            self._tcp_server.close()
            try:
                await self._tcp_server.wait_closed()
            except Exception:
                pass
            self._tcp_server = None

    async def reload(self):
        try:
            new_cfg = cfgmod.load(self.config_path)
        except cfgmod.ConfigError as exc:
            log.error("rechargement refusé, configuration conservée : %s", exc)
            return
        old = (self.cfg.listen_address, self.cfg.listen_port)
        self._apply_config(new_cfg)
        logging.getLogger("appalias").setLevel(new_cfg.log_level)
        await self.restore_tables()
        if (new_cfg.listen_address, new_cfg.listen_port) != old:
            await self.close_listeners()
            await self.open_listeners()
        self._log_upstream()
        log.info("configuration rechargée (%d applications)", len(self.apps))

    def _log_upstream(self):
        if self.cfg.upstream_protocol == "dot":
            log.info("amont en DNS over TLS : %s", ", ".join(
                "%s@%d#%s" % (u.host, u.port, u.tls_name) for u in self.cfg.upstreams))
        else:
            log.info("amont en DNS clair : %s", ", ".join(
                "%s@%d" % (u.host, u.port) for u in self.cfg.upstreams))

    def request_reload(self):
        self.spawn(self.reload())

    def request_stop(self):
        if self._stop is not None:
            self._stop.set()

    async def run(self, install_signals=True):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(MAX_INFLIGHT_QUERIES)
        self.state = State.load(self.state_path)
        # Tables restaurées AVANT d'ouvrir l'écoute.
        await self.restore_tables()
        await self.open_listeners()
        self._log_upstream()
        if install_signals:
            for sig, cb in ((getattr(signal, "SIGHUP", None), self.request_reload),
                            (getattr(signal, "SIGUSR1", None), self.request_ctl),
                            (signal.SIGTERM, self.request_stop),
                            (signal.SIGINT, self.request_stop)):
                if sig is None:
                    continue
                try:
                    self.loop.add_signal_handler(sig, cb)
                except (NotImplementedError, RuntimeError):
                    pass
        periodic = [
            asyncio.ensure_future(self._periodic(self.sweep_interval, self.sweep)),
            asyncio.ensure_future(self._periodic(self.save_interval, self.save_state)),
            asyncio.ensure_future(self._periodic(DOT_PRUNE_INTERVAL, self.prune_pools)),
            asyncio.ensure_future(self._periodic(METRICS_INTERVAL, self.save_metrics)),
            asyncio.ensure_future(self._periodic(discmod.DRAIN_INTERVAL, self.discovery_drain)),
            asyncio.ensure_future(self._periodic(discmod.SAVE_INTERVAL, self.discovery_save)),
        ]
        # Version du code en cours d'exécution, visible tout de suite dans l'état.
        self.save_metrics(force=True)
        self._proactive_task = self.spawn(self.proactive_loop())
        if self.ready is not None:
            self.ready.set()
        try:
            await self._stop.wait()
        finally:
            for task in periodic:
                task.cancel()
            await self.close_listeners()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)   # boucle et sondes proactives
            self._proactive_task = None
            self.close_pools()
            self.save_state()
            self.save_metrics()
            self._discovery_save_sync()
            log.info("arrêt : état sauvegardé")


def main(argv=None):
    # SIGUSR1 (commande de découverte) ignoré dès le départ : un signal reçu
    # avant l'installation du gestionnaire ne doit pas tuer le démon (action
    # par défaut). Le gestionnaire asyncio le remplace dans Relay.run.
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    parser = argparse.ArgumentParser(description="Relais DNS d'os-appalias")
    parser.add_argument("--config", default=cfgmod.CONFIG_PATH)
    parser.add_argument("--state", default=cfgmod.STATE_PATH)
    parser.add_argument("--stderr", action="store_true", help="journal sur stderr plutôt que syslog")
    args = parser.parse_args(argv)
    setup_logging(logging.INFO, use_syslog=not args.stderr)
    try:
        cfg = cfgmod.load(args.config)
    except cfgmod.ConfigError as exc:
        log.error("démarrage impossible, configuration invalide (nouvel essai par daemon -R 10) : %s",
                  exc)
        return EXIT_CONFIG
    logging.getLogger("appalias").setLevel(cfg.log_level)
    if not cfg.enabled:
        log.warning("le plugin est désactivé dans la configuration ; démarrage quand même")
    relay = Relay(cfg, PfTable(tmpdir=os.path.dirname(args.state) or None),
                  state_path=args.state, config_path=args.config)
    try:
        asyncio.run(relay.run())
    except OSError as exc:
        log.error("arrêt sur erreur : %s", exc)
        return EXIT_RUNTIME
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
