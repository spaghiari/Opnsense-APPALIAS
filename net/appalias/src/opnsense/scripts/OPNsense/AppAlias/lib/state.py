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

"""État du démon : IP résolues par table, avec leur expiration.

Expiration d'une IP : now + max(ttl, ttl_min) + ttl_margin.
Les CIDR manuels ne sont jamais stockés ici (ils viennent de la config).
L'écriture sur disque est atomique : fichier temporaire, fsync, puis rename.

Version 2 (0.10), en plus de la version 1 (relue telle quelle) :
- « names » : {table: {nom: dernier_vu}}, noms vus qui correspondent à la
  table (relais, sondes, amorçage Unbound) ; MAX_NAMES par table au plus (les
  plus anciens sortent), purgés après NAMES_MAX_AGE secondes sans être vus ;
- « wildcards » : {domaine: horodatage}, domaines détectés comme jokers DNS,
  oubliés après WILDCARD_MAX_AGE secondes, MAX_WILDCARDS au plus.
Un horodatage non fini (NaN, infini) est rejeté à la lecture.
"""

import json
import logging
import math
import os
import tempfile

STATE_VERSION = 2
MAX_NAMES = 1024                  # noms vus retenus par table
NAMES_MAX_AGE = 30 * 86400        # purge des noms non vus depuis 30 jours
WILDCARD_MAX_AGE = 7 * 86400      # durée de validité d'un verdict « joker »
MAX_WILDCARDS = 1024              # verdicts « joker » retenus (les plus récents)

log = logging.getLogger("appalias")


def _finite(value):
    """Horodatage float fini, ou None (valeur invalide, NaN ou infinie)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _most_recent(entries, limit):
    """Les limit entrées {clé: horodatage} les plus récentes."""
    keep = sorted(entries, key=lambda k: (-entries[k], k))[:limit]
    return {k: entries[k] for k in keep}


def compute_expiry(now, ttl, ttl_min, ttl_margin):
    return float(now) + max(int(ttl), int(ttl_min)) + int(ttl_margin)


class State:
    def __init__(self):
        self.tables = {}      # table -> {ip: expiration}
        self.last_hit = {}    # table -> horodatage
        self.last_error = {}  # table -> {"time": t, "message": m}
        self.names = {}       # table -> {nom: dernier_vu}
        self.wildcards = {}   # domaine joker -> horodatage de la détection

    # --- entrées ---------------------------------------------------------
    def is_known(self, table, ip, now):
        exp = self.tables.get(table, {}).get(ip)
        return exp is not None and exp > now

    def set(self, table, ip, expiry):
        """Ajoute ou rafraîchit (garde l'expiration la plus lointaine)."""
        entries = self.tables.setdefault(table, {})
        old = entries.get(ip)
        if old is None or expiry > old:
            entries[ip] = float(expiry)

    def remove(self, table, ip):
        entries = self.tables.get(table)
        if entries is not None:
            entries.pop(ip, None)

    def count(self, table):
        return len(self.tables.get(table, {}))

    def valid(self, table, now):
        return sorted(ip for ip, exp in self.tables.get(table, {}).items() if exp > now)

    def expired(self, now):
        """{table: [ip…]} des entrées expirées (sans les retirer)."""
        out = {}
        for table, entries in self.tables.items():
            ips = [ip for ip, exp in entries.items() if exp <= now]
            if ips:
                out[table] = sorted(ips)
        return out

    def pop_expired(self, now):
        out = self.expired(now)
        for table, ips in out.items():
            for ip in ips:
                self.tables[table].pop(ip, None)
        return out

    def drop_table(self, table):
        self.tables.pop(table, None)

    def prune_tables(self, keep):
        for table in list(self.tables):
            if table not in keep:
                del self.tables[table]
        for registry in (self.last_hit, self.last_error, self.names):
            for table in list(registry):
                if table not in keep:
                    del registry[table]

    def hit(self, table, now):
        self.last_hit[table] = float(now)

    def error(self, table, now, message):
        self.last_error[table] = {"time": float(now), "message": str(message)}

    def clear_error(self, table):
        self.last_error.pop(table, None)

    # --- noms vus --------------------------------------------------------
    def see(self, table, name, now):
        """Enregistre un nom vu pour la table ; vrai si le nom est nouveau.

        Au-delà de MAX_NAMES noms, les plus anciens sortent.
        """
        entries = self.names.setdefault(table, {})
        new = name not in entries
        entries[name] = float(now)
        if new and len(entries) > MAX_NAMES:
            for old in sorted(entries, key=lambda n: (entries[n], n))[:len(entries) - MAX_NAMES]:
                del entries[old]
            new = name in entries
        return new

    def names_count(self, table):
        return len(self.names.get(table, {}))

    def prune_names(self, now, max_age=NAMES_MAX_AGE):
        """Retire les noms non vus depuis max_age secondes ; renvoie le nombre retiré."""
        removed = 0
        limit = float(now) - max_age
        for table in list(self.names):
            entries = self.names[table]
            for name in [n for n, ts in entries.items() if ts < limit]:
                del entries[name]
                removed += 1
            if not entries:
                del self.names[table]
        return removed

    # --- jokers DNS ------------------------------------------------------
    def set_wildcard(self, domain, now):
        self.wildcards[domain] = float(now)
        if len(self.wildcards) > MAX_WILDCARDS:
            self.wildcards = _most_recent(self.wildcards, MAX_WILDCARDS)

    def clear_wildcard(self, domain):
        self.wildcards.pop(domain, None)

    def active_wildcards(self, now, max_age=WILDCARD_MAX_AGE):
        """Domaines jokers dont le verdict a moins de max_age secondes (triés)."""
        limit = float(now) - max_age
        return sorted(d for d, ts in self.wildcards.items() if ts >= limit)

    def prune_wildcards(self, now, max_age=WILDCARD_MAX_AGE):
        """Oublie les verdicts « joker » périmés ; renvoie le nombre retiré."""
        limit = float(now) - max_age
        old = [d for d, ts in self.wildcards.items() if ts < limit]
        for domain in old:
            del self.wildcards[domain]
        return len(old)

    # --- sérialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "version": STATE_VERSION,
            "tables": {t: dict(sorted(e.items())) for t, e in sorted(self.tables.items())},
            "last_hit": dict(sorted(self.last_hit.items())),
            "last_error": dict(sorted(self.last_error.items())),
            "names": {t: dict(sorted(e.items())) for t, e in sorted(self.names.items()) if e},
            "wildcards": dict(sorted(self.wildcards.items())),
        }

    @classmethod
    def from_dict(cls, data):
        st = cls()
        if not isinstance(data, dict):
            return st
        tables = data.get("tables", {})
        if isinstance(tables, dict):
            for table, entries in tables.items():
                if not isinstance(entries, dict):
                    continue
                clean = {}
                for ip, exp in entries.items():
                    try:
                        clean[str(ip)] = float(exp)
                    except (TypeError, ValueError):
                        continue
                st.tables[str(table)] = clean
        hits = data.get("last_hit", {})
        if isinstance(hits, dict):
            for table, ts in hits.items():
                try:
                    st.last_hit[str(table)] = float(ts)
                except (TypeError, ValueError):
                    continue
        errors = data.get("last_error", {})
        if isinstance(errors, dict):
            for table, err in errors.items():
                if isinstance(err, dict):
                    st.last_error[str(table)] = err
        # Version 2 : clés absentes d'un état v1 (lecture tolérante).
        names = data.get("names", {})
        if isinstance(names, dict):
            for table, entries in names.items():
                if not isinstance(entries, dict):
                    continue
                clean = {}
                for name, ts in entries.items():
                    ts = _finite(ts)
                    if ts is not None:
                        clean[str(name)] = ts
                if len(clean) > MAX_NAMES:
                    clean = _most_recent(clean, MAX_NAMES)
                if clean:
                    st.names[str(table)] = clean
        wild = data.get("wildcards", {})
        if isinstance(wild, dict):
            for domain, ts in wild.items():
                ts = _finite(ts)
                if ts is not None:
                    st.wildcards[str(domain)] = ts
            if len(st.wildcards) > MAX_WILDCARDS:
                st.wildcards = _most_recent(st.wildcards, MAX_WILDCARDS)
        return st

    def save(self, path):
        """Écriture atomique : temporaire dans le même dossier, puis rename."""
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.to_dict(), fh, indent=1, sort_keys=False)
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

    @classmethod
    def load(cls, path, logger=None):
        """Relit l'état ; fichier absent ou corrompu = état vide."""
        logger = logger or log
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("état illisible (%s), redémarrage à vide : %s", path, exc)
            return cls()
        return cls.from_dict(data)
