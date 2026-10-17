#!/usr/local/bin/python3
"""appaliasd : relais DNS d'os-appalias.

Chaque requête reçue (UDP ou TCP) est transmise telle quelle à l'amont
configuré. Dans la réponse, les adresses A/AAAA des noms qui correspondent
à une application sont ajoutées à la table pf de l'alias External de cette
application (APP_<NOM>), **avant** que la réponse soit rendue au client.

Concurrence : tous les appels pfctl d'une table (add, delete, replace,
flush) sont sérialisés par un verrou asyncio propre à la table, et l'état
n'est modifié qu'après le succès de l'appel correspondant.

Signaux : SIGHUP = relecture de config.json et remplacement des tables ;
SIGTERM/SIGINT = sauvegarde de l'état puis sortie.

Codes de sortie : 0 arrêt normal, 1 erreur d'exécution, 78 (EX_CONFIG)
configuration absente ou invalide. daemon(8) -R 10 relance le démon.
"""

import argparse
import asyncio
import ipaddress
import logging
import os
import signal
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import config as cfgmod  # noqa: E402
from lib import dnswire  # noqa: E402
from lib.matcher import Matcher  # noqa: E402
from lib.pftable import PfError, PfTable, normalize_entry  # noqa: E402
from lib.state import State, compute_expiry  # noqa: E402

log = logging.getLogger("appalias")

UDP_TIMEOUT = 2.0
TCP_TIMEOUT = 5.0
TCP_IDLE_TIMEOUT = 10.0
SWEEP_INTERVAL = 30.0
SAVE_INTERVAL = 60.0
PF_COOLDOWN = 30.0
MAX_INFLIGHT_QUERIES = 512
MAX_TCP_CLIENTS = 128

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
                 clock=time.time):
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
        self._apply_config(cfg)

    # -- configuration --
    def _apply_config(self, cfg):
        self.cfg = cfg
        self.apps = {a.table: a for a in cfg.apps}
        self.matcher = Matcher({a.table: a.domains for a in cfg.apps if a.enabled})

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
            resp = await self.forward(query, proto)
            if resp is None:
                return dnswire.make_servfail(query)
            try:
                await self.process_response(resp, expect=dnswire.question_key(query))
            except Exception as exc:  # la réponse part quoi qu'il arrive
                log.error("erreur interne au traitement d'une réponse : %s", exc)
            return resp

    async def forward(self, query, proto):
        for host, port in self.cfg.upstreams:
            try:
                if proto == "tcp":
                    return await asyncio.wait_for(self._tcp_exchange(query, host, port),
                                                  self.tcp_timeout)
                return await asyncio.wait_for(self._udp_exchange(query, host, port),
                                              self.udp_timeout)
            except (asyncio.TimeoutError, OSError, ConnectionError,
                    asyncio.IncompleteReadError) as exc:
                log.debug("amont %s:%s (%s) sans réponse : %r", host, port, proto, exc)
                continue
        log.warning("aucun amont n'a répondu (%s)", proto)
        return None

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
    async def process_response(self, raw, expect=_UNCHECKED):
        """Ajoute les IP de la réponse aux tables concernées.

        expect = (id, qname) de la requête (dnswire.question_key) : si la
        réponse ne correspond pas, rien n'est ajouté (l'appelant transmet la
        réponse quand même). expect=None (question de la requête illisible) :
        rien n'est ajouté. Sans expect : pas de contrôle.
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
            jobs.append(self._add(table, new, fut))
        if jobs or waits:
            await asyncio.gather(*jobs, *waits, return_exceptions=True)

    async def _add(self, table, new, fut):
        ips = [ip for ip, _ in new]
        try:
            async with self._lock(table):
                if await self._pf(table, self.pf.add, ips):
                    for ipstr, expiry in new:
                        self.state.set(table, ipstr, expiry)
                    log.info("%s : ajout de %s", table, " ".join(ips))
        finally:
            for ipstr, _ in new:
                self._inflight.pop((table, ipstr), None)
            if not fut.done():
                fut.set_result(None)

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
        log.info("configuration rechargée (%d applications)", len(self.apps))

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
        ]
        if self.ready is not None:
            self.ready.set()
        try:
            await self._stop.wait()
        finally:
            for task in periodic:
                task.cancel()
            await self.close_listeners()
            for task in list(self._tasks):
                task.cancel()
            self.save_state()
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
