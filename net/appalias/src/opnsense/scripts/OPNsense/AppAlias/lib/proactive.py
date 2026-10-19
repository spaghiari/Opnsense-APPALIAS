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

Module pur : aucune E/S ; l'horloge et l'aléa sont injectés (Scheduler), ce qui
rend le comportement déterministe en test. Le démon résout lui-même, à
intervalle régulier, les noms des applications activées pour remplir les alias
pf à l'avance (en complément du relais DNS).

Seules les règles « full:x » (sonde x) et « domain:x » (sonde x et chaque
préfixe de proactive_prefixes + "." + x) sont résolubles. Sont exclus :
« regexp: », « keyword: » et préfixes inconnus, les règles refusées par le
Matcher, les noms égaux à une plateforme partagée ou parents de l'une d'elles, et les
TLD seuls (`shared.excluded`) ; les sous-domaines dédiés sont sondés (lib/shared.py), les
applications désactivées ou dont « proactive » est faux.
"""

import heapq
import logging
import zlib

from . import shared
from .dnswire import TYPE_A, TYPE_AAAA, encode_name
from .matcher import KIND_DOMAIN, KIND_FULL, parse_rule

MAX_PER_APP = 256
MAX_TOTAL = 5000
FAIL_BACKOFF_START = 60
EMPTY_BACKOFF_MAX = 86400
JITTER_MIN = 0.8
JITTER_MAX = 1.0
# Au-delà de ce délai (s), une cible en vol jamais acquittée (done/defer) est
# traitée comme un échec : replanifiée avec le backoff d'échec.
INFLIGHT_GUARD = 120

OUTCOMES = ("ok", "nodata", "nxdomain", "fail")

log = logging.getLogger("appalias")


def _qtypes(families):
    if families == "inet":
        return {TYPE_A}
    if families == "inet6":
        return {TYPE_AAAA}
    return {TYPE_A, TYPE_AAAA}


def targets(cfg, matcher, platforms, logger=None):
    """Cibles {(nom, qtype): {tables}} à résoudre de façon proactive.

    Plafonds : MAX_PER_APP noms par application, MAX_TOTAL au total ; une seule
    ligne d'avertissement en cas de troncature.
    """
    logger = logger or log
    prefixes = [str(p).strip().strip(".").lower()
                for p in (getattr(cfg, "proactive_prefixes", None) or ())]
    prefixes = [p for p in prefixes if p]
    refused = set(getattr(matcher, "refused", ()) or ())
    out = {}
    all_names = set()
    truncated = False
    invalid = 0
    for app in cfg.apps:
        if not app.enabled or not getattr(app, "proactive", True):
            continue
        qtypes = _qtypes(getattr(app, "families", "both"))
        names = []
        seen = set()
        for rule in app.domains or ():
            if (app.table, rule) in refused:
                continue
            parsed = parse_rule(rule)
            if parsed is None or parsed[0] not in (KIND_FULL, KIND_DOMAIN):
                continue
            kind, value = parsed
            if shared.excluded(kind, value, platforms) is not None:
                continue
            candidates = [value]
            if kind == KIND_DOMAIN:
                candidates += [p + "." + value for p in prefixes]
            for name in candidates:
                if name in seen:
                    continue
                seen.add(name)
                try:
                    encode_name(name)
                except ValueError:
                    invalid += 1
                    continue
                names.append(name)
        if len(names) > MAX_PER_APP:
            names = names[:MAX_PER_APP]
            truncated = True
        for name in names:
            if name not in all_names:
                if len(all_names) >= MAX_TOTAL:
                    truncated = True
                    continue
                all_names.add(name)
            for qtype in qtypes:
                out.setdefault((name, qtype), set()).add(app.table)
    if invalid:
        logger.debug("résolution proactive : %d nom(s) invalide(s) écarté(s) (non encodable en DNS)", invalid)
    if truncated:
        logger.warning("résolution proactive : liste tronquée (%d noms par application, "
                       "%d au total au plus)", MAX_PER_APP, MAX_TOTAL)
    return out


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

    def load(self, targets_):
        """Remplace la liste : échéances conservées pour les cibles inchangées,
        nouvelles cibles étalées sur interval_min, cibles disparues retirées."""
        now = self.clock()
        keys = set(targets_)
        for key in list(self._state):
            if key not in keys:
                del self._state[key]
        for key in sorted(keys):
            if key not in self._state:
                self._state[key] = {"due": 0.0, "fail": 0.0, "empty": 0.0,
                                    "inflight": False, "inflight_since": None}
                self._push(key, now + self._spread(key))
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
            base = min(max(ttl if ttl else lo, lo), hi)
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
