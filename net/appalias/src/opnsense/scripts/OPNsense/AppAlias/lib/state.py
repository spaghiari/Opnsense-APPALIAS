"""État du démon : IP résolues par table, avec leur expiration.

Expiration d'une IP : now + max(ttl, ttl_min) + ttl_margin.
Les CIDR manuels ne sont jamais stockés ici (ils viennent de la config).
L'écriture sur disque est atomique : fichier temporaire, fsync, puis rename.
"""

import json
import logging
import os
import tempfile

STATE_VERSION = 1

log = logging.getLogger("appalias")


def compute_expiry(now, ttl, ttl_min, ttl_margin):
    return float(now) + max(int(ttl), int(ttl_min)) + int(ttl_margin)


class State:
    def __init__(self):
        self.tables = {}      # table -> {ip: expiration}
        self.last_hit = {}    # table -> horodatage
        self.last_error = {}  # table -> {"time": t, "message": m}

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
        for registry in (self.last_hit, self.last_error):
            for table in list(registry):
                if table not in keep:
                    del registry[table]

    def hit(self, table, now):
        self.last_hit[table] = float(now)

    def error(self, table, now, message):
        self.last_error[table] = {"time": float(now), "message": str(message)}

    def clear_error(self, table):
        self.last_error.pop(table, None)

    # --- sérialisation ---------------------------------------------------
    def to_dict(self):
        return {
            "version": STATE_VERSION,
            "tables": {t: dict(sorted(e.items())) for t, e in sorted(self.tables.items())},
            "last_hit": dict(sorted(self.last_hit.items())),
            "last_error": dict(sorted(self.last_error.items())),
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
