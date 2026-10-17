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

"""Moteur de découverte du relais (désactivé par défaut).

Rien n'est jamais ajouté à une application : le moteur se contente de
compter des noms DNS, que discovery.py (configd, hors démon) transforme en
propositions à valider par l'utilisateur. Aucune information sur les clients :
le relais ne voit que le résolveur local (Unbound) ; seuls des noms et des
horodatages sont gardés.

Ce que voit le relais : avec le branchement automatique d'Unbound (une zone
par domaine d'application), seulement les domaines déjà transférés et leurs
chaînes CNAME (et les sous-domaines des règles full:). Le moteur sert donc
surtout aux cibles CNAME, aux sous-domaines, aux CDN et à la co-occurrence
entre ces noms ; il ne découvre des domaines sans rapport que si tout le DNS
passe par le relais (transfert manuel « . », Dnsmasq).

Trois étages :

1. Observer.observe(), sur le chemin de réponse : un append dans une file
   bornée (QUEUE_MAX), sans calcul ni écriture disque ; file pleine =
   événement perdu et compté (dropped).
2. Engine.drain(), tâche périodique (DRAIN_INTERVAL) : lots de BATCH_MAX
   événements au plus, la main rendue à la boucle tous les YIELD_EVERY.
   Les noms couverts sont comptés tout de suite (passages par application) ;
   les noms non couverts attendent la fenêtre W, pour que la co-occurrence
   ±W voie aussi les passages qui les suivent.
3. Persistance : discovery.json (écriture atomique, 0640) toutes les
   SAVE_INTERVAL secondes et à l'arrêt ; purge des noms plus vieux que la
   rétention au chargement et toutes les PURGE_INTERVAL secondes.

Mode Learn : session bornée (LEARN_MAX_SECONDS, LEARN_MAX_EVENTS) qui note
les noms non couverts vus pendant la fenêtre (« new » s'ils n'avaient jamais
été vus avant), puis s'arrête seule.

Commandes (SIGUSR1) : discovery.ctl.json, écrit par discovery.py (configd),
JSON strict validé par parse_ctl : dump, purge, learn, stop_learn.
"""

import asyncio
import bisect
import json
import logging
import math
import os
import re
import tempfile
import time
from collections import deque

DISCOVERY_PATH = "/var/db/appalias/discovery.json"
CTL_PATH = "/var/db/appalias/discovery.ctl.json"
FILE_VERSION = 1

QUEUE_MAX = 8192
BATCH_MAX = 2000
YIELD_EVERY = 500
SLICE_SECONDS = 0.01     # au-delà, la main est rendue à la boucle sans attendre YIELD_EVERY
SLICE_CHECK = 32         # fréquence de la mesure du temps (événements)
SNAPSHOT_CHUNK = 2000    # entrées sérialisées par tranche dans snapshot_json()
DRAIN_INTERVAL = 2.0
SAVE_INTERVAL = 600.0
PURGE_INTERVAL = 3600.0

MAX_TARGETS = 4          # cibles CNAME gardées par nom
MAX_LINKS = 8            # liens de chaîne vers des noms couverts, par nom
MAX_COOC = 8             # applications en co-occurrence, par nom
MAX_PASSAGES = 64        # derniers passages gardés par application
MAX_TARGET_NAMES = 8     # noms couverts par cible CNAME de l'index
MAX_PENDING = 20000      # noms non couverts en attente de la fenêtre

LEARN_MAX_SECONDS = 600
LEARN_MAX_EVENTS = 20000
MAX_CTL_BYTES = 4096

TYPE_A = 1
TYPE_AAAA = 28

CTL_COMMANDS = ("dump", "purge", "learn", "stop_learn")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
NAME_RE = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$")
TABLE_RE = re.compile(r"^[A-Za-z0-9_]{1,31}$")

log = logging.getLogger("appalias")


# --- étage 1 : chemin de réponse ----------------------------------------
class Observer:
    """File bornée d'événements DNS ; observe() ne fait qu'un append."""

    __slots__ = ("queue", "maxlen", "dropped")

    def __init__(self, maxlen=QUEUE_MAX):
        self.queue = deque()
        self.maxlen = maxlen
        self.dropped = 0

    def observe(self, qname, chain, answers, now, tables):
        """Appelé par Relay.process_response, une fois la chaîne calculée.

        answers : enregistrements de la réponse (comptés plus tard, hors du
        chemin de réponse) ; tables : tables des applications concernées.
        """
        if len(self.queue) >= self.maxlen:
            self.dropped += 1
            return
        self.queue.append((now, qname, chain, answers, tables))


# --- règles ignorées -----------------------------------------------------
class Ignored:
    """Règles full:/domain: ignorées (réglage general.discovery_ignored)."""

    def __init__(self, rules=()):
        self.full = set()
        self.domain = set()
        for kind, value in rules or ():
            (self.full if kind == "full" else self.domain).add(value)

    def __bool__(self):
        return bool(self.full or self.domain)

    def match(self, name):
        if name in self.full:
            return True
        if not self.domain:
            return False
        labels = name.split(".")
        return any(".".join(labels[i:]) in self.domain for i in range(len(labels)))


# --- étages 2 et 3 -------------------------------------------------------
def _addr_count(answers, chain):
    wanted = set(chain)
    return sum(1 for rec in answers
               if rec.rtype in (TYPE_A, TYPE_AAAA) and rec.name in wanted)


class Engine:
    """Agrégation des événements, persistance et mode Learn.

    match_name : fonction nom -> ensemble de tables (Matcher du relais).
    """

    def __init__(self, match_name, window=5, max_names=5000, retention_days=7,
                 ignored=None, clock=None, path=DISCOVERY_PATH):
        self.match_name = match_name
        self.window = max(1, int(window))
        self.max_names = max(1, int(max_names))
        self.retention_days = max(1, int(retention_days))
        self.ignored = ignored or Ignored()
        self.clock = clock or time.time
        self.path = path
        self.observer = Observer()
        self.pending = deque()
        self.passages = {}      # table -> deque(horodatages)
        self.clear()

    # -- état --
    def clear(self):
        """Mémoire vidée (purge, désactivation)."""
        self.generation = getattr(self, "generation", 0) + 1
        self.observer.queue.clear()
        self.pending.clear()
        self.passages = {}
        self.names = {}         # nom non couvert -> statistiques
        self.covered = {}       # nom couvert -> dernière vue
        self.targets = {}       # cible CNAME -> {"last": t, "names": [[table, nom]]}
        self.apps = {}          # table -> {"active": fenêtres actives, "bucket": dernière}
        self.windows_total = 0
        self._last_bucket = None
        self.processed = 0
        self.dropped_base = self.observer.dropped
        self.learn = None
        self.started = self.clock()
        self.dirty = True

    def configure(self, window, max_names, retention_days, ignored, match_name):
        self.window = max(1, int(window))
        self.max_names = max(1, int(max_names))
        self.retention_days = max(1, int(retention_days))
        self.ignored = ignored or Ignored()
        self.match_name = match_name

    @property
    def dropped(self):
        return self.observer.dropped - self.dropped_base

    # -- agrégation --
    def _bucket(self, t):
        return int(t // self.window)

    def _event(self, t, chain, answers, tables):
        if not chain or _addr_count(answers, chain) == 0:
            return
        self.processed += 1
        bucket = self._bucket(t)
        if bucket != self._last_bucket:
            self._last_bucket = bucket
            self.windows_total += 1
        for table in tables:
            pas = self.passages.get(table)
            if pas is None:
                pas = self.passages[table] = deque(maxlen=MAX_PASSAGES)
            # passages triés (bisect dans _cooccurring) ; le plus ancien sort en premier
            if pas and t < pas[-1]:
                if len(pas) == pas.maxlen:
                    pas.popleft()
                bisect.insort(pas, t)
            else:
                pas.append(t)
            app = self.apps.setdefault(table, {"active": 0, "bucket": None})
            if app["bucket"] != bucket:
                app["bucket"] = bucket
                app["active"] += 1
        matched = [self.match_name(name) for name in chain]
        links = []
        for i, name in enumerate(chain):
            if not matched[i]:
                continue
            self.covered[name] = t
            for table in sorted(matched[i]):
                if len(links) < MAX_LINKS:
                    links.append((table, name))
            if i + 1 < len(chain):
                self._index_target(chain[i + 1], matched[i], name, t)
        if self.learn is not None and self.learn["active"]:
            if self.learn["start"] <= t <= self.learn["until"]:
                self.learn["events"] += 1
        for i, name in enumerate(chain):
            if matched[i] or self.ignored.match(name):
                continue
            target = chain[i + 1] if i + 1 < len(chain) else None
            if len(self.pending) >= MAX_PENDING:
                self.pending.popleft()
            self.pending.append((t, name, target, tuple(links), frozenset(tables)))

    def _index_target(self, target, tables, name, t):
        entry = self.targets.get(target)
        if entry is None:
            entry = self.targets[target] = {"last": t, "names": []}
        entry["last"] = max(entry["last"], t)
        for table in sorted(tables):
            pair = [table, name]
            if pair not in entry["names"] and len(entry["names"]) < MAX_TARGET_NAMES:
                entry["names"].append(pair)

    def _cooccurring(self, t, exclude):
        """Tables avec un passage dans [t - W, t + W], hors tables de l'événement.

        Passages triés : une recherche dichotomique par table (O(log 64))."""
        lo, hi = t - self.window, t + self.window
        out = []
        for table, pas in self.passages.items():
            if table in exclude:
                continue
            i = bisect.bisect_left(pas, lo)
            if i < len(pas) and pas[i] <= hi:
                out.append(table)
        return sorted(out)

    def _settle(self, item):
        t, name, target, links, tables = item
        stat = self.names.get(name)
        if stat is None:
            stat = self.names[name] = {"views": 0, "first": t, "last": t,
                                       "targets": [], "links": [], "cooc": {}}
        stat["views"] += 1
        stat["first"] = min(stat["first"], t)
        stat["last"] = max(stat["last"], t)
        if target and target not in stat["targets"] and len(stat["targets"]) < MAX_TARGETS:
            stat["targets"].append(target)
        for table, covered in links:
            pair = [table, covered]
            if pair not in stat["links"] and len(stat["links"]) < MAX_LINKS:
                stat["links"].append(pair)
        cooc = stat["cooc"]
        for table in self._cooccurring(t, tables):
            if table in cooc or len(cooc) < MAX_COOC:
                cooc[table] = cooc.get(table, 0) + 1
        learn = self.learn
        if learn is not None and learn["start"] <= t <= learn["until"]:
            seen = learn["names"].get(name)
            if seen is None:
                if len(learn["names"]) < LEARN_MAX_EVENTS:
                    learn["names"][name] = {"new": stat["first"] >= learn["start"], "views": 1}
            else:
                seen["views"] += 1

    async def drain(self, now=None):
        """Un passage de la tâche périodique ; renvoie le nombre d'événements lus."""
        queue = self.observer.queue
        done = 0
        mark = time.monotonic()
        while queue and done < BATCH_MAX:
            t, _qname, chain, answers, tables = queue.popleft()
            self._event(t, chain, answers, tables)
            done += 1
            if done % YIELD_EVERY == 0 or (done % SLICE_CHECK == 0
                                           and time.monotonic() - mark > SLICE_SECONDS):
                await asyncio.sleep(0)
                mark = time.monotonic()
        now = self.clock() if now is None else now
        limit = now - self.window
        settled = 0
        while self.pending and self.pending[0][0] <= limit and settled < BATCH_MAX:
            self._settle(self.pending.popleft())
            settled += 1
            if settled % YIELD_EVERY == 0 or (settled % SLICE_CHECK == 0
                                              and time.monotonic() - mark > SLICE_SECONDS):
                await asyncio.sleep(0)
                mark = time.monotonic()
        if done or settled:
            self.dirty = True
            self._evict()
        self._learn_tick(now)
        return done

    def _evict(self):
        """Au-delà de max_names : retire 10 % des noms les plus faibles (vues, dernière vue)."""
        for registry, key in ((self.names, lambda kv: (kv[1]["views"], kv[1]["last"])),
                              (self.covered, lambda kv: kv[1]),
                              (self.targets, lambda kv: kv[1]["last"])):
            if len(registry) <= self.max_names:
                continue
            keep = max(0, self.max_names - max(1, math.ceil(self.max_names / 10)))
            weakest = sorted(registry.items(), key=key)[:len(registry) - keep]
            for name, _value in weakest:
                del registry[name]

    def purge_old(self, now=None):
        """Retire les noms vus pour la dernière fois avant la rétention."""
        now = self.clock() if now is None else now
        limit = now - self.retention_days * 86400
        before = len(self.names) + len(self.covered) + len(self.targets)
        for name in [n for n, s in self.names.items() if s["last"] < limit]:
            del self.names[name]
        for name in [n for n, t in self.covered.items() if t < limit]:
            del self.covered[name]
        for name in [n for n, e in self.targets.items() if e["last"] < limit]:
            del self.targets[name]
        removed = before - len(self.names) - len(self.covered) - len(self.targets)
        if removed:
            self.dirty = True
        return removed

    # -- mode Learn --
    def start_learn(self, until, app_uuid=None, now=None):
        now = self.clock() if now is None else now
        until = min(float(until), now + LEARN_MAX_SECONDS)
        self.learn = {"start": now, "until": until, "app_uuid": app_uuid or None,
                      "active": until > now, "events": 0, "names": {}, "stopped": None}
        self.dirty = True

    def stop_learn(self, reason="user", now=None):
        if self.learn is not None and self.learn["active"]:
            now = self.clock() if now is None else now
            self.learn["active"] = False
            self.learn["stopped"] = reason
            self.learn["until"] = min(self.learn["until"], now)
            self.dirty = True

    def _learn_tick(self, now):
        learn = self.learn
        if learn is None or not learn["active"]:
            return
        if learn["events"] >= LEARN_MAX_EVENTS:
            self.stop_learn("events", now)
        elif now > learn["until"] + self.window:
            # fenêtre écoulée et noms en attente traités
            learn["active"] = False
            learn["stopped"] = "time"
            self.dirty = True

    # -- sérialisation --
    async def snapshot_json(self, now=None):
        """discovery.json sérialisé par tranches de SNAPSHOT_CHUNK entrées, la
        main rendue à la boucle entre deux tranches.

        Chaque tranche devient tout de suite du texte JSON : les chaînes ne sont
        pas suivies par le ramasse-miettes, alors qu'une copie profonde de
        50 000 noms (dictionnaires et listes) déclenche une collecte complète de
        plusieurs dizaines de millisecondes sur la boucle, donc sur le DNS.
        Une entrée modifiée entre deux tranches est écrite dans son état du moment.
        """
        now = self.clock() if now is None else now
        head = self._envelope(now, {}, {}, {})
        del head["names"], head["covered"], head["targets"]
        parts = [json.dumps(head, separators=(",", ":"))[:-1]]
        for key, registry in (("names", self.names), ("covered", self.covered),
                              ("targets", self.targets)):
            items = list(registry.items())
            chunks = []
            for start in range(0, len(items), SNAPSHOT_CHUNK):
                text = json.dumps(dict(items[start:start + SNAPSHOT_CHUNK]), separators=(",", ":"))
                chunks.append(text[1:-1])
                await asyncio.sleep(0)
            parts.append(',"%s":{%s}' % (key, ",".join(c for c in chunks if c)))
        parts.append("}\n")
        return "".join(parts)

    def to_dict(self, now=None):
        """Instantané complet en une fois (arrêt du démon, tests, discovery.py)."""
        now = self.clock() if now is None else now
        names = {n: _copy_stat(s) for n, s in self.names.items()}
        targets = {t: {"last": e["last"], "names": [list(p) for p in e["names"]]}
                   for t, e in self.targets.items()}
        return self._envelope(now, names, dict(self.covered), targets)

    def _envelope(self, now, names, covered, targets):
        learn = None
        if self.learn is not None:
            learn = dict(self.learn)
            learn["names"] = {n: dict(v) for n, v in self.learn["names"].items()}
        return {
            "version": FILE_VERSION,
            "saved": now,
            "started": self.started,
            "window": self.window,
            "retention_days": self.retention_days,
            "max_names": self.max_names,
            "windows_total": self.windows_total,
            "processed": self.processed,
            "dropped": self.dropped,
            "queued": len(self.observer.queue) + len(self.pending),
            "apps": {t: {"active": a["active"]} for t, a in sorted(self.apps.items())},
            "names": names,
            "covered": covered,
            "targets": targets,
            "learn": learn,
        }

    def load_dict(self, data):
        """Reprend un discovery.json ; toute entrée invalide est écartée."""
        self.clear()
        if not isinstance(data, dict) or data.get("version") != FILE_VERSION:
            return False
        self.started = _num(data.get("started"), self.started)
        self.windows_total = int(_num(data.get("windows_total"), 0))
        self.processed = int(_num(data.get("processed"), 0))
        for table, app in _items(data.get("apps")):
            if TABLE_RE.match(table) and isinstance(app, dict):
                self.apps[table] = {"active": int(_num(app.get("active"), 0)), "bucket": None}
        for name, stat in _items(data.get("names")):
            clean = _clean_stat(name, stat)
            if clean is not None:
                self.names[name] = clean
        for name, last in _items(data.get("covered")):
            if NAME_RE.match(name) and _is_num(last):
                self.covered[name] = float(last)
        for target, entry in _items(data.get("targets")):
            if not NAME_RE.match(target) or not isinstance(entry, dict) or not _is_num(entry.get("last")):
                continue
            pairs = _pairs(entry.get("names"), MAX_TARGET_NAMES)
            if pairs:
                self.targets[target] = {"last": float(entry["last"]), "names": pairs}
        self.learn = _clean_learn(data.get("learn"))
        if self.learn is not None:
            self.learn["active"] = False    # une session ne survit pas à un redémarrage
            self.learn["stopped"] = self.learn["stopped"] or "restart"
        self.dirty = False
        return True

    def load(self, path=None):
        """Relit discovery.json ; absent ou corrompu = vide. Purge la rétention."""
        path = path or self.path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            self.clear()
            return False
        except (OSError, ValueError) as exc:
            log.warning("découverte : %s illisible, données remises à zéro : %s", path, exc)
            self.clear()
            return False
        ok = self.load_dict(data)
        if not ok:
            log.warning("découverte : %s invalide, données remises à zéro", path)
        self.purge_old()
        return ok


def _copy_stat(s):
    return {"views": s["views"], "first": s["first"], "last": s["last"],
            "targets": list(s["targets"]), "links": [list(p) for p in s["links"]],
            "cooc": dict(s["cooc"])}


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(value, default):
    return float(value) if _is_num(value) else default


def _items(value):
    if not isinstance(value, dict):
        return []
    return [(str(k), v) for k, v in value.items() if isinstance(k, str)]


def _pairs(value, limit):
    out = []
    if not isinstance(value, list):
        return out
    for pair in value:
        if (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
                and isinstance(pair[1], str) and TABLE_RE.match(pair[0]) and NAME_RE.match(pair[1])):
            if pair not in out and len(out) < limit:
                out.append([pair[0], pair[1]])
    return out


def _clean_stat(name, stat):
    if not NAME_RE.match(name) or not isinstance(stat, dict):
        return None
    if not all(_is_num(stat.get(k)) for k in ("views", "first", "last")):
        return None
    targets = [t for t in stat.get("targets") or [] if isinstance(t, str) and NAME_RE.match(t)]
    cooc = {}
    for table, count in _items(stat.get("cooc")):
        if TABLE_RE.match(table) and _is_num(count) and len(cooc) < MAX_COOC:
            cooc[table] = int(count)
    return {"views": int(stat["views"]), "first": float(stat["first"]), "last": float(stat["last"]),
            "targets": targets[:MAX_TARGETS], "links": _pairs(stat.get("links"), MAX_LINKS),
            "cooc": cooc}


def _clean_learn(value):
    if not isinstance(value, dict):
        return None
    if not _is_num(value.get("start")) or not _is_num(value.get("until")):
        return None
    uuid = value.get("app_uuid")
    names = {}
    for name, item in _items(value.get("names")):
        if NAME_RE.match(name) and isinstance(item, dict) and len(names) < LEARN_MAX_EVENTS:
            names[name] = {"new": item.get("new") is True, "views": int(_num(item.get("views"), 1))}
    stopped = value.get("stopped")
    return {"start": float(value["start"]), "until": float(value["until"]),
            "app_uuid": uuid if isinstance(uuid, str) and UUID_RE.match(uuid) else None,
            "active": False, "events": int(_num(value.get("events"), 0)), "names": names,
            "stopped": stopped if stopped in ("user", "time", "events", "restart") else None}


# --- fichiers ------------------------------------------------------------
def write_json(path, data):
    """Écriture atomique (temporaire dans le même dossier, fsync, rename), 0640."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".discovery.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, separators=(",", ":"), sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o640)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_text(path, text):
    """Écriture atomique d'un texte déjà sérialisé (snapshot_json), 0640."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".discovery.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o640)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove_file(path):
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def ctl_path_for(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)), os.path.basename(CTL_PATH))


def _reject_constant(value):
    raise ValueError("constante JSON refusée : %s" % value)


def parse_ctl(text, now):
    """Commande de discovery.ctl.json -> dict validé, ou ValueError.

    {"cmd": "dump" | "purge" | "stop_learn"}
    {"cmd": "learn", "until": t, "app_uuid": "..."}   until <= now + 600
    Aucune autre clé n'est acceptée.
    """
    if not isinstance(text, str) or len(text) > MAX_CTL_BYTES:
        raise ValueError("commande trop longue")
    data = json.loads(text, parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise ValueError("objet JSON attendu")
    cmd = data.get("cmd")
    if cmd not in CTL_COMMANDS:
        raise ValueError("commande inconnue")
    allowed = {"cmd"} | ({"until", "app_uuid"} if cmd == "learn" else set())
    extra = set(data) - allowed
    if extra:
        raise ValueError("clé inattendue : %s" % ", ".join(sorted(map(str, extra))))
    out = {"cmd": cmd}
    if cmd == "learn":
        until = data.get("until")
        if not _is_num(until) or until <= now or until > now + LEARN_MAX_SECONDS:
            raise ValueError("until hors plage (maintenant + %d s au plus)" % LEARN_MAX_SECONDS)
        uuid = data.get("app_uuid")
        if uuid not in (None, "") and (not isinstance(uuid, str) or not UUID_RE.match(uuid)):
            raise ValueError("app_uuid invalide")
        out["until"] = float(until)
        out["app_uuid"] = uuid or None
    return out
