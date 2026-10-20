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

Résolution proactive (lib/proactive.py) : une tâche de fond résout elle-même,
à intervalle régulier, les noms des applications activées (au plus
PROACTIVE_MAX_CONCURRENT sondes simultanées, PROACTIVE_RATE par seconde). Les
réponses passent par process_response(hit=False) : jamais de
« dernier usage », jamais de latence client.

Cache (lib/cache.py, cache_enabled) : une réponse déjà en cache est rendue
sans interroger l'amont, mais passe quand même par process_response (l'IP
est dans la table avant la réponse). Seules les réponses de l'amont qui
correspondent à la question sont mises en cache, jamais un SERVFAIL
synthétisé. TTL plafonné à ttl_min + ttl_margin ; cache vidé à la
désactivation.

Clients (lib/clients.py) : une requête dont l'adresse source est hors des
réseaux clients (client_networks, réseaux privés et bouclage par défaut) est
ignorée sans réponse (pas d'amplification) ; au-delà de client_rate_limit
requêtes par seconde et par client, elle est ignorée aussi (le bouclage et
les adresses d'écoute du relais ne sont jamais limités). En TCP, la connexion
est fermée ; une connexion dont l'adresse source est inconnue est refusée.

ASN (lib/asn.py, asn_enabled) : les préfixes annoncés par les ASN d'une
application, lus dans le cache ASN (asn_update.py), sont posés dans la table
avec les CIDR manuels ; une IP DNS déjà couverte par un préfixe ASN n'est
pas ajoutée. Ces préfixes ne sont jamais dans l'état et comptent dans le
plafond max_entries_per_app (tronqués au besoin, avertissement journalisé).

Backend pf (lib/pftable.make_backend, pf_backend) : ioctl sur /dev/pf ou
pfctl, choisi au démarrage seulement (un changement demande un redémarrage) ;
le backend actif, le mode demandé au démarrage et la cause d'un repli sur
pfctl sont écrits dans metrics.json. Si le backend ioctl échoue en cours de
route sur une erreur de structure (EINVAL, ENODEV, ENOTTY, EFAULT), le démon
bascule définitivement sur pfctl et rejoue l'appel une fois.

Écoute : une socket UDP et un serveur TCP par adresse de listen_addresses ;
une adresse qui ne peut pas être ouverte est sautée (erreur journalisée) et
réessayée toutes les LISTEN_RETRY_INTERVAL secondes ; le démarrage échoue
seulement si aucune ne l'a été. Un rechargement qui ne peut ouvrir aucune des
nouvelles adresses rouvre les anciennes ; si cela échoue aussi, le démon
s'arrête (daemon(8) le relance).

Version : le démon écrit la version de son code, son PID et son heure de
démarrage dans metrics.json dès le démarrage (status.py la compare à la
version installée).

Signaux : SIGHUP = relecture de config.json et remplacement des tables ;
SIGTERM/SIGINT = sauvegarde de l'état puis sortie.

Codes de sortie : 0 arrêt normal, 1 erreur d'exécution, 78 (EX_CONFIG)
configuration absente ou invalide. daemon(8) -R 10 relance le démon.
"""

import argparse
import asyncio
import bisect
import errno
import ipaddress
import logging
import os
import random
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import asn  # noqa: E402
from lib import cache  # noqa: E402
from lib import config as cfgmod  # noqa: E402
from lib import dnswire  # noqa: E402
from lib import dot  # noqa: E402
from lib import metrics  # noqa: E402
from lib import proactive  # noqa: E402
from lib import shared  # noqa: E402
from lib.clients import ClientAcl, RateLimiter  # noqa: E402
from lib.matcher import Matcher  # noqa: E402
from lib.pftable import PfError, PfTable, make_backend, normalize_entry  # noqa: E402
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
LISTEN_RETRY_INTERVAL = 60.0     # nouvel essai des adresses d'écoute en échec
# errno d'un ioctl pf qui trahit une structure mal comprise par le noyau : repli pfctl.
PF_FALLBACK_ERRNOS = frozenset((errno.EINVAL, errno.ENODEV, errno.ENOTTY, errno.EFAULT))
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
        self.closed = asyncio.get_running_loop().create_future()   # socket réellement fermée

    def connection_made(self, transport):
        self.transport = transport

    def connection_lost(self, exc):
        if not self.closed.done():
            self.closed.set_result(None)

    def datagram_received(self, data, addr):
        if not self.relay.accept_client(addr[0]):
            return  # rien n'est renvoyé : pas d'amplification
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
                 clock=time.time, tls_cafile=dot.CA_BUNDLE, asn_cache_path=asn.CACHE_PATH):
        self.cfg = cfg
        self.pf = pf
        self.asn_cache_path = asn_cache_path
        self.pf_backend_mode = cfg.pf_backend   # backend choisi au démarrage seulement
        # Backend de repli à chaud (ioctl en échec) ; remplaçable par les tests.
        self.pfctl_factory = lambda: PfTable(tmpdir=os.path.dirname(state_path) or None)
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
        self._udp_transports = []
        self._tcp_servers = []
        self._listening = []     # adresses effectivement ouvertes
        self._listen_spec = None         # (adresses, port) demandés à la dernière ouverture
        self._listen_missing = set()     # adresses de _listen_spec pas encore ouvertes
        self.listen_retry_interval = LISTEN_RETRY_INTERVAL
        self._sem = None
        self.started = time.time()
        self._config_gen = 0     # incrémenté à chaque configuration appliquée
        self.sched = None        # proactive.Scheduler
        self._proactive_targets = {}   # (nom, qtype) -> {tables}
        self._proactive_wake = None    # asyncio.Event, créé dans la boucle
        self._proactive_active = 0     # sondes en cours
        self._proactive_task = None
        self._proactive_rng = random.Random()
        self.cache = cache.Cache(clock=clock)
        self.acl = ClientAcl()
        self.limiter = None
        self._asn_prefixes = {}  # table -> préfixes ASN (texte canonique)
        self._asn_nets = {}      # table -> {version: (débuts, fins)} triés, pour bisect
        self._asn_cap_logged = set()
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
        self._update_clients(cfg)
        self._update_cache(cfg)
        self._update_asn(cfg)
        if cfg.pf_backend != self.pf_backend_mode:
            log.info("backend pf : redémarrage nécessaire pour appliquer %s", cfg.pf_backend)
        self._update_proactive(cfg)

    def _update_clients(self, cfg):
        """ACL reconstruite ; limiteur recréé seulement si le débit change (seaux conservés)."""
        self.acl = ClientAcl(cfg.client_networks)
        if self.limiter is None or self.limiter.rate != float(cfg.client_rate_limit or 0):
            self.limiter = RateLimiter(cfg.client_rate_limit)
        # Unbound sort par l'IP du pare-feu quand aucune adresse de bouclage n'écoute.
        self.limiter.set_exempt(cfg.listen_addresses)

    def _update_cache(self, cfg):
        self.cache.max_ttl = min(86400, cfg.ttl_min + cfg.ttl_margin)
        if not cfg.cache_enabled:
            self.cache.clear()

    def _update_asn(self, cfg):
        """Préfixes ASN par table, relus dans le cache ASN (vide si asn_enabled est faux)."""
        old = {t: len(p) for t, p in self._asn_prefixes.items()}
        if cfg.asn_enabled:
            prefixes = asn.prefixes_by_table(cfg, asn.load_cache(self.asn_cache_path))
        else:
            prefixes = {}
        apps = {a.table: a for a in cfg.apps}
        for table, items in list(prefixes.items()):
            app = apps.get(table)
            limit = max(cfg.max_entries_per_app - (len(app.networks) if app else 0), 0)
            if len(items) > limit:
                if table not in self._asn_cap_logged:
                    log.warning("%s : %d préfixes ASN, tronqués à %d (plafond de %d entrées)",
                                table, len(items), limit, cfg.max_entries_per_app)
                    self._asn_cap_logged.add(table)
                prefixes[table] = items[:limit]
            else:
                self._asn_cap_logged.discard(table)
        self._asn_prefixes = prefixes
        self._asn_nets = {t: self._intervals(p) for t, p in prefixes.items() if p}
        self.metrics.set_asn(sum(1 for p in prefixes.values() if p),
                             sum(len(p) for p in prefixes.values()))
        for table in sorted(set(old) | set(prefixes)):
            count = len(prefixes.get(table, ()))
            if old.get(table, 0) != count:
                log.info("%s : %d préfixes ASN", table, count)

    @staticmethod
    def _intervals(prefixes):
        """{version: (débuts, fins)} : intervalles fusionnés et triés des préfixes."""
        spans = {4: [], 6: []}
        for prefix in prefixes:
            try:
                net = ipaddress.ip_network(prefix, strict=False)
            except ValueError:
                continue
            spans[net.version].append((int(net.network_address), int(net.broadcast_address)))
        out = {}
        for version, items in spans.items():
            starts, ends = [], []
            for start, end in sorted(items):
                if ends and start <= ends[-1] + 1:
                    ends[-1] = max(ends[-1], end)
                else:
                    starts.append(start)
                    ends.append(end)
            out[version] = (starts, ends)
        return out

    def _asn_covered(self, table, ip):
        """Vrai si l'IP est couverte par un préfixe ASN de la table (recherche dichotomique)."""
        nets = self._asn_nets.get(table)
        if not nets:
            return False
        starts, ends = nets.get(ip.version, ((), ()))
        value = int(ip)
        i = bisect.bisect_right(starts, value) - 1
        return i >= 0 and value <= ends[i]

    def _manual_entries(self, app):
        """CIDR manuels puis préfixes ASN de l'application, sans doublon, ordre conservé."""
        manual = [normalize_entry(n) for n in app.networks]
        return list(dict.fromkeys(manual + self._asn_prefixes.get(app.table, [])))

    # -- clients --
    def accept_client(self, ip):
        """Vrai si le client ip peut être servi (réseau autorisé, débit respecté)."""
        if not self.acl.allowed(ip):
            self.metrics.add_client("refused")
            log.debug("requête de %s ignorée : hors des réseaux clients", ip)
            return False
        return self._rate_ok(ip)

    def _rate_ok(self, ip):
        if self.limiter.allow(ip):
            return True
        self.metrics.add_client("rate_limited")
        self._warn_limited(("client", ip), "client %s : débit de %d requêtes/s dépassé, "
                           "requêtes ignorées" % (ip, self.cfg.client_rate_limit))
        return False

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
        return (app.family_ok(ip) and not self.cfg.excluded(ip) and not app.covered(ip)
                and not self._asn_covered(app.table, ip))

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
            try:
                await loop.run_in_executor(None, func, table, *args)
            except PfError as exc:
                retry = self._pf_fallback(func, exc)
                if retry is None:
                    raise
                await loop.run_in_executor(None, retry, table, *args)   # un seul nouvel essai
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

    def _pf_fallback(self, func, exc):
        """Méthode équivalente du backend pfctl si l'échec ioctl justifie la bascule, sinon None.

        Bascule définitive : self.pf devient un PfTable (fallback_reason posé).
        Un appel lancé sur l'ancien backend avant la bascule est rejoué sur le
        nouveau sans nouvelle bascule.
        """
        owner = getattr(func, "__self__", None)
        name = getattr(func, "__name__", None)
        if (name is None or getattr(owner, "name", None) != "ioctl"
                or getattr(exc, "errno", None) not in PF_FALLBACK_ERRNOS):
            return None
        if self.pf is owner:
            try:
                backend = self.pfctl_factory()
            except Exception as err:
                log.error("repli sur pfctl impossible : %s", err)
                return None
            reason = str(exc)
            try:
                backend.fallback_reason = reason
            except AttributeError:
                pass
            self.pf = backend
            log.error("backend pf ioctl en échec (%s) : bascule définitive sur pfctl", reason)
        if getattr(self.pf, "name", None) == "ioctl":
            return None
        return getattr(self.pf, name, None)

    async def restore_tables(self):
        """Remplace chaque table par IP DNS valides + CIDR manuels + préfixes ASN.

        Les tables des applications désactivées sont vidées.

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
                manual = self._manual_entries(app)
                if await self._pf(table, self.pf.replace, manual + kept, force=True):
                    log.info("%s : table remplacée (%d IP DNS, %d CIDR manuels, %d préfixes ASN)",
                             table, len(kept), len(app.networks),
                             len(self._asn_prefixes.get(table, ())))

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
            expect = dnswire.question_key(query)
            use_cache = self.cfg.cache_enabled
            if use_cache:
                hit = self.cache.get(query)
                if hit is not None:
                    self.metrics.add_cache(True)
                    try:
                        await self.process_response(hit, expect=expect)
                    except Exception as exc:  # la réponse part quoi qu'il arrive
                        log.error("erreur interne au traitement d'une réponse : %s", exc)
                    # Réponse en cache (UDP, TCP ou DoT) : bornée à la taille UDP du client.
                    return dnswire.fit_udp(query, hit) if proto == "udp" else hit
                self.metrics.add_cache(False)
            timing = {}
            resp = await self.forward(query, proto, timing)
            if resp is None:
                return dnswire.make_servfail(query)   # jamais mis en cache
            try:
                await self.process_response(resp, expect=expect,
                                            upstream_ms=timing.get("upstream_ms"))
            except Exception as exc:  # la réponse part quoi qu'il arrive
                log.error("erreur interne au traitement d'une réponse : %s", exc)
            if use_cache and expect is not None and dnswire.question_key(resp) == expect:
                self.cache.put(query, resp)   # seulement une réponse à cette question
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
        peer = writer.get_extra_info("peername")
        ip = peer[0] if isinstance(peer, (tuple, list)) and peer and isinstance(peer[0], str) else None
        if ip is None or not self.acl.allowed(ip):
            self.metrics.add_client("refused")
            log.debug("connexion TCP de %s fermée : hors des réseaux clients", ip or "(inconnu)")
            writer.close()
            return
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
                if not self._rate_ok(ip):
                    break                       # débit dépassé : connexion fermée
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
                               hit=True, source="relay"):
        """Ajoute les IP de la réponse aux tables concernées.

        expect = (id, qname) de la requête (dnswire.question_key) : si la
        réponse ne correspond pas, rien n'est ajouté (l'appelant transmet la
        réponse quand même). expect=None (question de la requête illisible) :
        rien n'est ajouté. Sans expect : pas de contrôle. upstream_ms : temps
        de l'amont, repris dans la ligne de journal des ajouts.

        Options (défauts = comportement du relais) : hit=False ne met pas à jour le « dernier usage »
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
            room = (self.cfg.max_entries_per_app - self.state.count(table) - len(app.networks)
                    - len(self._asn_prefixes.get(table, ())))
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
        await self.process_response(raw, expect, hit=False, source="proactive")
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
                manual = set(self._manual_entries(app)) if app else set()
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
        return {"version": VERSION, "pid": os.getpid(), "started": int(self.started),
                "pf_backend": getattr(self.pf, "name", "pfctl"),
                "pf_backend_mode": self.pf_backend_mode,
                "pf_backend_reason": getattr(self.pf, "fallback_reason", None)}

    def save_metrics(self, force=False):
        """Écrit metrics.json si une mesure a changé depuis la dernière écriture."""
        self.metrics.set_cache_entries(len(self.cache))
        if self.metrics.version == self._metrics_saved and not force:
            return
        try:
            metrics.write(self.metrics_path, dict(self.metrics.to_dict(), time=int(time.time()),
                                                  daemon=self.daemon_info()))
            self._metrics_saved = self.metrics.version
        except OSError as exc:
            log.debug("écriture des mesures impossible (%s) : %s", self.metrics_path, exc)

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
    @staticmethod
    def _endpoint(addr, port):
        return "[%s]:%d" % (addr, port) if ":" in addr else "%s:%d" % (addr, port)

    async def _open_one(self, addr, port):
        """UDP puis TCP sur addr:port ; OSError (rien ne reste ouvert) en cas d'échec."""
        loop = asyncio.get_running_loop()
        udp, _ = await loop.create_datagram_endpoint(
            lambda: _UdpServer(self), local_addr=(addr, port))
        try:
            tcp = await asyncio.start_server(self._tcp_client, addr, port)
        except OSError:
            udp.close()
            raise
        self._udp_transports.append(udp)
        self._tcp_servers.append(tcp)
        self._listening.append(self._endpoint(addr, port))

    async def open_listeners(self, addresses=None, port=None):
        """UDP et TCP sur chaque adresse (celles de la configuration par défaut).

        Une adresse en échec est journalisée et notée pour retry_listeners ;
        OSError si aucune n'a pu être ouverte (rien n'est alors ouvert).
        """
        if addresses is None:
            addresses = self.cfg.listen_addresses or [self.cfg.listen_address]
        port = self.cfg.listen_port if port is None else port
        missing = set()
        for addr in addresses:
            try:
                await self._open_one(addr, port)
            except OSError as exc:
                log.error("écoute impossible sur %s : %s", self._endpoint(addr, port), exc)
                missing.add(addr)
        if not self._listening:
            self._listen_missing = set()
            raise OSError("aucune adresse d'écoute n'a pu être ouverte (port %d)" % port)
        self._listen_spec = (tuple(addresses), port)
        self._listen_missing = missing
        log.info("écoute sur %s (UDP et TCP)", ", ".join(self._listening))

    async def retry_listeners(self):
        """Nouvel essai des adresses d'écoute qui n'avaient pas pu être ouvertes."""
        if self._listen_spec is None or not self._listen_missing or not self._listening:
            return
        port = self._listen_spec[1]
        for addr in sorted(self._listen_missing):
            try:
                await self._open_one(addr, port)
            except OSError as exc:
                log.debug("écoute toujours impossible sur %s : %s", self._endpoint(addr, port), exc)
                continue
            self._listen_missing.discard(addr)
            log.info("écoute ouverte sur %s (adresse devenue disponible)", self._endpoint(addr, port))

    async def close_listeners(self):
        # La socket UDP n'est fermée qu'à une itération suivante de la boucle :
        # on attend connection_lost pour pouvoir rouvrir aussitôt le même port.
        waits = []
        for transport in self._udp_transports:
            transport.close()
            protocol = transport.get_protocol()
            if isinstance(protocol, _UdpServer):
                waits.append(protocol.closed)
        self._udp_transports = []
        if waits:
            try:
                await asyncio.wait_for(asyncio.gather(*waits), 2.0)
            except asyncio.TimeoutError:
                pass
        servers, self._tcp_servers = self._tcp_servers, []
        for server in servers:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
        self._listening = []
        self._listen_missing = set()

    async def reload(self):
        try:
            new_cfg = cfgmod.load(self.config_path)
        except cfgmod.ConfigError as exc:
            log.error("rechargement refusé, configuration conservée : %s", exc)
            return
        old = self._listen_spec
        self._apply_config(new_cfg)
        logging.getLogger("appalias").setLevel(new_cfg.log_level)
        await self.restore_tables()
        if (tuple(new_cfg.listen_addresses), new_cfg.listen_port) != old:
            await self._reopen_listeners(old)
        self._log_upstream()
        log.info("configuration rechargée (%d applications)", len(self.apps))

    async def _reopen_listeners(self, old):
        """Nouvelles adresses d'écoute ; anciennes rouvertes si aucune ne peut l'être."""
        await self.close_listeners()
        try:
            await self.open_listeners()
            return
        except OSError as exc:
            log.error("rechargement : aucune nouvelle adresse d'écoute ouverte (%s), "
                      "anciennes adresses conservées", exc)
        try:
            if old is None:
                raise OSError("aucune adresse d'écoute précédente")
            await self.open_listeners(list(old[0]), old[1])
        except OSError as exc:
            log.critical("écoute impossible, arrêt du relais (relance par daemon(8)) : %s", exc)
            self.request_stop()

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
            asyncio.ensure_future(self._periodic(self.listen_retry_interval, self.retry_listeners)),
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
            log.info("arrêt : état sauvegardé")


def main(argv=None):
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
    try:
        pf = make_backend(cfg.pf_backend, tmpdir=os.path.dirname(args.state) or None)
    except (PfError, OSError, ImportError) as exc:
        log.error("backend pf « %s » indisponible : %s", cfg.pf_backend, exc)
        return EXIT_RUNTIME
    log.info("backend pf : %s", getattr(pf, "name", "pfctl"))
    relay = Relay(cfg, pf, state_path=args.state, config_path=args.config)
    try:
        asyncio.run(relay.run())
    except OSError as exc:
        log.error("arrêt sur erreur : %s", exc)
        return EXIT_RUNTIME
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
