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

"""Mesures de latence du relais : temps de l'amont et temps de pfctl.

Fenêtre glissante bornée (WINDOW dernières mesures) par amont et pour pf ;
sur le chemin de réponse, une mesure coûte un append dans un deque, sans
écriture disque. Le démon écrit le résumé (to_dict) périodiquement dans
metrics.json, à côté de state.json ; status.py le relit.

Résumé d'une fenêtre : count (mesures depuis le démarrage), window (mesures
de la fenêtre), avg_ms et p95_ms (rang le plus proche) sur la fenêtre.

Résolution proactive : compteurs depuis le démarrage (champ optionnel
« proactive », absent tant qu'aucune sonde n'a été envoyée) : sent (requêtes
réellement envoyées à l'amont : un rejeu TCP après troncature compte pour une
requête de plus ; les sondes reportées par cooldown ou abandonnées avant tout
envoi ne comptent pas), ok, empty (réponse sans adresse ou NXDOMAIN), fail,
added (adresses ajoutées aux tables par le proactif).
Relation : sent = ok + empty + fail + rejets « stale » (réponse jetée car la
configuration a changé pendant la sonde) + rejeux TCP intermédiaires (la
requête UDP tronquée est comptée dans sent sans résultat propre). fail compte
aussi les sondes échouées avant tout envoi (nom invalide, erreur interne) : dans
ce cas fail dépasse sent d'autant.
"""

import json
import math
import os
import tempfile
from collections import deque

WINDOW = 256
FILENAME = "metrics.json"
PROACTIVE_COUNTERS = ("sent", "ok", "empty", "fail", "added")


class Window:
    def __init__(self, size=WINDOW):
        self.values = deque(maxlen=size)
        self.count = 0

    def add(self, ms):
        self.values.append(float(ms))
        self.count += 1

    def summary(self):
        n = len(self.values)
        if n == 0:
            return {"count": self.count, "window": 0, "avg_ms": None, "p95_ms": None}
        ordered = sorted(self.values)
        p95 = ordered[max(0, math.ceil(0.95 * n) - 1)]
        return {"count": self.count, "window": n, "avg_ms": round(sum(ordered) / n, 1),
                "p95_ms": round(p95, 1)}


class Metrics:
    def __init__(self, size=WINDOW):
        self.size = size
        self.upstreams = {}      # « host@port » -> Window
        self.pf = Window(size)
        self.proactive = dict.fromkeys(PROACTIVE_COUNTERS, 0)
        self.version = 0         # incrémenté à chaque mesure (écriture si changé)

    def add_upstream(self, key, ms):
        win = self.upstreams.get(key)
        if win is None:
            win = self.upstreams[key] = Window(self.size)
        win.add(ms)
        self.version += 1

    def add_pf(self, ms):
        self.pf.add(ms)
        self.version += 1

    def add_proactive(self, counter, n=1):
        """Incrémente un compteur de la résolution proactive (PROACTIVE_COUNTERS)."""
        if counter in self.proactive and n > 0:
            self.proactive[counter] += n
            self.version += 1

    def retain(self, keys):
        """Oublie les amonts qui ne sont plus configurés."""
        for key in list(self.upstreams):
            if key not in keys:
                del self.upstreams[key]
                self.version += 1

    def to_dict(self):
        out = {"window": self.size,
               "upstreams": {k: w.summary() for k, w in sorted(self.upstreams.items())},
               "pf": self.pf.summary()}
        if self.proactive["sent"]:
            out["proactive"] = dict(self.proactive)
        return out


def path_for(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), FILENAME)


def write(path, data):
    """Écriture atomique (temporaire puis rename)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".metrics.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
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


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
