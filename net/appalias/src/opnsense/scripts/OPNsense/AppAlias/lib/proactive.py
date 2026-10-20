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

"""Résolution proactive : choix des noms à sonder et planification.

Module pur : aucune E/S hors lecture des fichiers de données (load_labels,
load_hints) ; l'horloge et l'aléa sont injectés (Scheduler), ce qui rend le
comportement déterministe en test. Le démon résout lui-même, à intervalle
régulier, les noms des applications activées pour remplir les alias pf à
l'avance (en complément du relais DNS).

Seules les règles « full:x » (sonde x) et « domain:x » sont résolubles. Les
cibles d'une application sont, par ordre de priorité :
- x, puis, pour « domain:x », chaque préfixe de proactive_prefixes + "." + x ;
- les indices (data/probe_hints.txt) des listes du catalogue référencées par
  l'application, seulement s'ils correspondent à ses règles ;
- les noms déjà vus pour la table (relais, sondes, amorçage Unbound), les
  MAX_SEEN_PROBED plus récents, seulement s'ils correspondent encore aux
  règles et ne sont pas sous un joker connu ; une cible issue des seuls noms
  vus est « lente » (report["slow"]) : le Scheduler la sert après les autres
  et la replanifie au rythme de interval_max ;
- en découverte profonde (proactive_deep), label + "." + x pour chaque label
  du dictionnaire (data/probe_labels.txt) et chaque « domain:x », sauf si x
  est un joker DNS connu ou inexistant (« missing » : NXDOMAIN sur x) et,
  quand l'appelant fournit l'ensemble des domaines vérifiés (« clear »),
  seulement une fois x vérifié non joker.
Les plafonds (MAX_PER_APP, MAX_TOTAL) servent d'abord les noms de base (tout
sauf le dictionnaire), puis le dictionnaire.

Détection des jokers : en découverte profonde, chaque x qui n'est pas un joker
connu reçoit une cible spéciale (x, QTYPE_WILDCARD), que le démon traduit en
une requête A sur un nom aléatoire « <12 hex>.x » : des adresses dans la
réponse font de x un joker (ses cibles de dictionnaire sont retirées).

Sont exclus : « regexp: », « keyword: » et préfixes inconnus, les règles
refusées par le Matcher, les noms égaux à une plateforme partagée ou parents
de l'une d'elles, et les TLD seuls (`shared.excluded`) ; les sous-domaines
dédiés sont sondés (lib/shared.py), les applications désactivées ou dont
« proactive » est faux.
"""

import heapq
import logging
import os
import re
import zlib

from . import shared
from .dnswire import TYPE_A, TYPE_AAAA, encode_name, normalize_name
from .matcher import KIND_DOMAIN, KIND_FULL, parse_rule

MAX_PER_APP = 2048
MAX_TOTAL = 20000
MAX_LABELS = 128
MAX_SEEN_PROBED = 256       # noms vus re-sondés par table (les plus récents)
FAIL_BACKOFF_START = 60
EMPTY_BACKOFF_MAX = 86400
JITTER_MIN = 0.8
JITTER_MAX = 1.0
# Au-delà de ce délai (s), une cible en vol jamais acquittée (done/defer) est
# traitée comme un échec : replanifiée avec le backoff d'échec.
INFLIGHT_GUARD = 120
# Pseudo-type des cibles « sonde joker de x » (0 est réservé en DNS : jamais
# envoyé tel quel, le démon interroge en A un nom aléatoire sous x).
QTYPE_WILDCARD = 0

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LABELS_PATH = os.path.join(DATA_DIR, "probe_labels.txt")
HINTS_PATH = os.path.join(DATA_DIR, "probe_hints.txt")

OUTCOMES = ("ok", "nodata", "nxdomain", "fail")

_LABEL = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?$")
_LIST = re.compile(r"^[a-z0-9][a-z0-9!._-]*$")

log = logging.getLogger("appalias")


def _qtypes(families):
    if families == "inet":
        return {TYPE_A}
    if families == "inet6":
        return {TYPE_AAAA}
    return {TYPE_A, TYPE_AAAA}


def family_qtypes(families):
    """Types de requête (A, AAAA) d'une famille d'adresses (« both », « inet », « inet6 »)."""
    return set(_qtypes(families))


def _read_lines(path, what, logger):
    """Lignes utiles d'un fichier de données (commentaires « # » retirés), ou None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError) as exc:     # ValueError : UTF-8 invalide
        logger.warning("%s : lecture impossible de %s : %s", what, path, exc)
        return None
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def load_labels(path=LABELS_PATH, logger=None):
    """Dictionnaire de labels de la découverte profonde : liste ordonnée, sans
    doublon, MAX_LABELS au plus. Fichier absent ou illisible : liste vide
    (avertissement) ; labels invalides ignorés."""
    logger = logger or log
    lines = _read_lines(path, "dictionnaire de labels", logger)
    if lines is None:
        return []
    out = []
    bad = 0
    for line in lines:
        for item in line.split():
            label = item.strip().strip(".").lower()
            if not _LABEL.match(label):
                bad += 1
                continue
            if label in out:
                continue
            if len(out) >= MAX_LABELS:
                logger.warning("dictionnaire de labels : %d labels au plus, suite ignorée",
                               MAX_LABELS)
                return out
            out.append(label)
    if bad:
        logger.warning("dictionnaire de labels : %d label(s) invalide(s) ignoré(s)", bad)
    return out


def load_hints(path=HINTS_PATH, logger=None):
    """Indices par liste du catalogue : {liste: [noms]} (ordre conservé, sans
    doublon). Lignes « <liste>: nom1 nom2 ... » ; fichier absent ou illisible :
    dictionnaire vide (avertissement) ; noms invalides ignorés."""
    logger = logger or log
    lines = _read_lines(path, "indices de résolution", logger)
    if lines is None:
        return {}
    out = {}
    for line in lines:
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip().lower()
        if not _LIST.match(key):
            continue
        names = out.setdefault(key, [])
        for item in rest.split():
            name = normalize_name(item)
            if not name or "." not in name or not _encodable(name) or name in names:
                continue
            names.append(name)
    return {k: v for k, v in out.items() if v}


def ref_list(ref):
    """Nom de liste d'une référence du catalogue (« netflix@-ads » -> « netflix »)."""
    return str(ref).split("@", 1)[0].strip().lower()


def under_wildcard(name, wildcards):
    """Vrai si name est un sous-domaine strict d'un domaine joker de wildcards."""
    if not wildcards:
        return False
    labels = name.split(".")
    return any(".".join(labels[i:]) in wildcards for i in range(1, len(labels)))


def _encodable(name):
    try:
        encode_name(name)
    except ValueError:
        return False
    return True


def targets(cfg, matcher, platforms, logger=None, *, labels=(), hints=None, seen=None,
            wildcards=(), clear=None, missing=(), report=None):
    """Cibles {(nom, qtype): {tables}} à résoudre de façon proactive.

    labels : dictionnaire de la découverte profonde (vide = pas de découverte
    profonde) ; hints : {liste: [noms]} ; seen : {table: {nom: dernier_vu}} ;
    wildcards : domaines jokers connus (pas de dictionnaire, noms vus en
    dessous ignorés) ; clear : domaines vérifiés non jokers, ou None (pas de
    vérification exigée : dictionnaire ajouté pour tout domaine qui n'est pas
    un joker connu) ; missing : domaines qui répondent NXDOMAIN (ni sonde
    joker ni dictionnaire). Les cibles (x, QTYPE_WILDCARD) sont les sondes
    joker.

    Plafonds : MAX_PER_APP noms par application, MAX_TOTAL au total (noms de
    base d'abord, dictionnaire ensuite). report (dict) : reçoit « truncated »
    (bool) et « slow » (clés issues des seuls noms vus) ; sans report, une
    ligne d'avertissement en cas de troncature.
    """
    logger = logger or log
    prefixes = [str(p).strip().strip(".").lower()
                for p in (getattr(cfg, "proactive_prefixes", None) or ())]
    prefixes = [p for p in prefixes if p]
    labels = list(labels or ())
    deep = bool(getattr(cfg, "proactive_deep", True)) and bool(labels)
    hints = hints or {}
    seen = seen or {}
    wildcards = set(wildcards or ())
    missing = set(missing or ())
    refused = set(getattr(matcher, "refused", ()) or ())
    out = {}
    truncated = False
    invalid = 0
    plans = []
    seen_only = set()       # noms issus des seuls noms vus, pour au moins une application
    not_slow = set()        # noms de base ou de dictionnaire, pour au moins une application
    for app in cfg.apps:
        if not app.enabled or not getattr(app, "proactive", True):
            continue
        table = app.table
        qtypes = _qtypes(getattr(app, "families", "both"))
        base, deep_names = [], []
        known = set()

        def add(name, dest):
            nonlocal invalid
            if name in known:
                return
            known.add(name)
            if not _encodable(name):
                invalid += 1
                return
            dest.append(name)

        def in_rules(name, table=table):
            return (table in matcher.match_name(name)
                    and shared.excluded(KIND_FULL, name, platforms) is None)

        domains = []
        for rule in app.domains or ():
            if (table, rule) in refused:
                continue
            parsed = parse_rule(rule)
            if parsed is None or parsed[0] not in (KIND_FULL, KIND_DOMAIN):
                continue
            kind, value = parsed
            if shared.excluded(kind, value, platforms) is not None:
                continue
            add(value, base)
            if kind == KIND_DOMAIN:
                if value not in domains:
                    domains.append(value)
                for p in prefixes:
                    add(p + "." + value, base)
        for ref in getattr(app, "catalog_refs", None) or ():
            for name in hints.get(ref_list(ref), ()):
                if name not in known and in_rules(name):
                    add(name, base)
        dictionary = set()
        if deep:
            for value in domains:
                if value not in wildcards and value not in missing:
                    dictionary.update(label + "." + value for label in labels)
        table_seen = seen.get(table) or {}
        app_seen_only = set()
        probed = 0
        for name in sorted(table_seen, key=lambda n: (-float(table_seen[n]), n)):
            if probed >= MAX_SEEN_PROBED:
                break
            if name in known or under_wildcard(name, wildcards) or not in_rules(name):
                continue
            probed += 1
            if name not in dictionary:
                app_seen_only.add(name)
            add(name, base)
        seen_only |= app_seen_only
        not_slow.update(known - app_seen_only)
        if deep:
            for value in domains:
                if value in wildcards or value in missing:
                    continue
                if _encodable("a" * 12 + "." + value):
                    out.setdefault((value, QTYPE_WILDCARD), set()).add(table)
                if clear is not None and value not in clear:
                    continue
                for label in labels:
                    add(label + "." + value, deep_names)
        names = base + deep_names
        if len(names) > MAX_PER_APP:
            names = names[:MAX_PER_APP]
            truncated = True
        nb_base = min(len(base), len(names))
        plans.append((table, qtypes, names[:nb_base], names[nb_base:]))
    all_names = set()
    for phase in (0, 1):            # noms de base de toutes les applications, puis dictionnaire
        for table, qtypes, base_part, deep_part in plans:
            for name in (deep_part if phase else base_part):
                if name not in all_names:
                    if len(all_names) >= MAX_TOTAL:
                        truncated = True
                        continue
                    all_names.add(name)
                for qtype in qtypes:
                    out.setdefault((name, qtype), set()).add(table)
    if invalid:
        logger.debug("résolution proactive : %d nom(s) invalide(s) écarté(s) (non encodable en DNS)",
                     invalid)
    if report is not None:
        report["truncated"] = truncated
        slow = seen_only - not_slow
        report["slow"] = {k for k in out if k[0] in slow}
    elif truncated:
        logger.warning("résolution proactive : liste tronquée (%d noms par application, "
                       "%d au total au plus)", MAX_PER_APP, MAX_TOTAL)
    return out


def names_by_table(targets_):
    """{table: nombre de noms distincts à sonder} (sondes joker exclues)."""
    names = {}
    for (name, qtype), tables in targets_.items():
        if qtype == QTYPE_WILDCARD:
            continue
        for table in tables:
            names.setdefault(table, set()).add(name)
    return {t: len(n) for t, n in names.items()}


def count_names(targets_):
    """Nombre de noms distincts à sonder (sondes joker exclues)."""
    return len({name for name, qtype in targets_ if qtype != QTYPE_WILDCARD})


class Scheduler:
    """Planification des cibles : tas (échéance, nom, qtype).

    Cycle d'une cible : due() la sort et la marque « en vol » ; l'appelant doit
    ensuite appeler EXACTEMENT UN de done() ou defer(). defer() remplace done()
    pour une cible renvoyée par due() (cooldown pfctl) : il libère le « en vol »
    et reprogramme la cible à l'instant voulu, sans toucher au backoff. Une
    cible en vol depuis plus de INFLIGHT_GUARD secondes sans acquittement est
    traitée comme un échec (backoff d'échec) et redevient éligible.
    """

    def __init__(self, clock, interval_min, interval_max, rng):
        self.clock = clock
        self.interval_min = interval_min
        self.interval_max = max(interval_max, interval_min)
        self.rng = rng
        self._state = {}  # clé -> {"due", "fail", "empty" (backoffs), "inflight", "inflight_since"}
        self._heap = []

    def __len__(self):
        return len(self._state)

    def _push(self, key, due):
        st = self._state[key]
        st["due"] = due
        st["inflight"] = False
        st["inflight_since"] = None
        heapq.heappush(self._heap, (due, key[0], key[1]))

    def _spread(self, key):
        """Décalage initial déterministe dans [0, interval_min[ (hachage du nom)."""
        h = zlib.crc32(("%s/%d" % key).encode("utf-8")) % 1000
        return self.interval_min * h / 1000.0

    def load(self, targets_, spread=False, slow=()):
        """Remplace la liste : échéances conservées pour les cibles inchangées,
        cibles disparues retirées.

        Une nouvelle cible est due tout de suite (première passe immédiate :
        l'ordre du tas et le débit du démon la servent) ; spread=True (démarrage
        du démon) l'étale plutôt sur interval_min. Les sondes joker sont
        toujours servies en tête. Les cibles de slow (issues des seuls noms
        vus) sont « lentes » : première échéance après interval_min (derrière
        les nouvelles cibles de base), puis replanifiées au rythme de
        interval_max. Les replanifications (done) restent étalées par la gigue.
        """
        now = self.clock()
        keys = set(targets_)
        slow = set(slow or ())
        for key in list(self._state):
            if key not in keys:
                del self._state[key]
        for key in sorted(keys):
            if key in self._state:
                self._state[key]["slow"] = key in slow
            else:
                self._state[key] = {"due": 0.0, "fail": 0.0, "empty": 0.0,
                                    "inflight": False, "inflight_since": None,
                                    "slow": key in slow}
                offset = self._spread(key) if spread else 0.0
                if key[1] == QTYPE_WILDCARD:
                    due = now - 1.0         # sondes joker servies avant les autres cibles
                elif key in slow:
                    due = now + self.interval_min + offset
                else:
                    due = now + offset
                self._push(key, due)
        # purge des entrées de tas périmées (cibles retirées)
        self._heap = [e for e in self._heap
                      if (e[1], e[2]) in self._state
                      and self._state[(e[1], e[2])]["due"] == e[0]
                      and not self._state[(e[1], e[2])]["inflight"]]
        heapq.heapify(self._heap)

    def due(self, now, limit):
        """Au plus limit cibles échues, marquées en vol (à acquitter par done ou defer)."""
        for key, st in list(self._state.items()):
            since = st["inflight_since"]
            if st["inflight"] and since is not None and now - since >= INFLIGHT_GUARD:
                self.done(key, now, "fail")
        out = []
        while self._heap and len(out) < limit and self._heap[0][0] <= now:
            due, name, qtype = heapq.heappop(self._heap)
            key = (name, qtype)
            st = self._state.get(key)
            if st is None or st["inflight"] or st["due"] != due:
                continue
            st["inflight"] = True
            st["inflight_since"] = now
            out.append(key)
        return out

    def done(self, key, now, outcome, ttl=None):
        """Replanifie une cible après résolution. Cible retirée : ignoré.

        Deux backoffs distincts : échecs (« fail ») et réponses vides
        (« nodata »/« nxdomain »). « ok » remet les deux à zéro ; un changement
        de catégorie repart de la base de la nouvelle catégorie.
        """
        st = self._state.get(key)
        if st is None:
            return
        lo, hi = self.interval_min, self.interval_max
        if outcome == "ok":
            st["fail"] = st["empty"] = 0.0
            base = hi if st.get("slow") else min(max(ttl if ttl else lo, lo), hi)
            delay = base * self.rng.uniform(JITTER_MIN, JITTER_MAX)
        elif outcome in ("nodata", "nxdomain"):
            st["fail"] = 0.0
            st["empty"] = hi if st["empty"] < hi else min(st["empty"] * 2, EMPTY_BACKOFF_MAX)
            delay = st["empty"]
        else:
            st["empty"] = 0.0
            st["fail"] = (FAIL_BACKOFF_START if st["fail"] <= 0
                          else min(st["fail"] * 2, hi))
            delay = st["fail"]
        self._push(key, now + delay)

    def defer(self, key, until):
        """Reporte une cible (cooldown pfctl d'une table). Inconnue : ignoré.

        Remplace done() pour une cible renvoyée par due() : libère le « en vol »
        et reprogramme à « until » ; les backoffs sont conservés.
        """
        if key in self._state:
            self._push(key, until)

    def next_due(self):
        """Prochaine échéance (cibles en vol : fin du délai de garde), ou None."""
        guard = [st["inflight_since"] + INFLIGHT_GUARD for st in self._state.values()
                 if st["inflight"] and st["inflight_since"] is not None]
        heap_due = self._next_heap_due()
        cands = guard + ([heap_due] if heap_due is not None else [])
        return min(cands) if cands else None

    def _next_heap_due(self):
        while self._heap:
            due, name, qtype = self._heap[0]
            st = self._state.get((name, qtype))
            if st is None or st["inflight"] or st["due"] != due:
                heapq.heappop(self._heap)
                continue
            return due
        return None
