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

"""Suivi automatique du catalogue v2fly, par application (catalog_follow).

Calcul seulement : catalog_follow.php (configd) garde le verrou de
config.xml, lit et enregistre le modèle avec ses validations, et appelle
catalog_follow.py, qui délègue ici. Un seul endroit pour la logique, testé
par tests/test_follow.py ; le PHP ne fait que transporter les listes.

Par application suivie (catalog_follow=1, catalog_refs non vide) :

- ajout = résolution(catalog_refs) - catalog_rules, sans les règles déjà
  dans domains, ni les règles refusées à l'import (catalog_declined), ni
  celles que l'import aurait décochées : garde-fou (domain: sur une
  plateforme partagée ou un TLD seul), nom qui touche une plateforme
  partagée, keyword:/regexp: (règles non appliquées), virgule. Les règles @ads sont
  déjà exclues par la référence (« netflix@-ads »).
- retrait = catalog_rules - résolution : seulement des règles venues du
  catalogue ; les règles ajoutées à la main (domains - catalog_rules) ne
  sont jamais touchées.
- plafond : au-delà de MAX_APP_RULES règles dans domains, rien n'est
  appliqué pour l'application (capped).

Annulation (undo) d'une entrée du journal : les règles ajoutées sont
retirées et notées dans catalog_declined (le prochain suivi ne les remet
pas) ; les règles retirées reviennent comme règles ajoutées à la main (le
prochain suivi ne les retire pas).

Journal : JOURNAL_PATH, JOURNAL_MAX entrées au plus (les plus anciennes
sortent), écriture atomique sous verrou.
"""

import json
import os
import re
import secrets
import tempfile
import time

from . import shared
from . import v2fly

JOURNAL_PATH = "/var/db/appalias/catalog_follow.json"
JOURNAL_VERSION = 1
JOURNAL_MAX = 200
LOG_DEFAULT = 20
MAX_APP_RULES = 1000
MAX_RULE_LENGTH = 1100
MAX_LIST = 20000

ENTRY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KINDS = ("follow", "undo")


class FollowError(ValueError):
    """Entrée refusée ; rendue en JSON ({"error": ...})."""


# --- listes -------------------------------------------------------------
def rule_list(value, limit=MAX_LIST):
    """Liste de règles (chaînes non vides, sans doublon, ordre gardé) ; FollowError sinon."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise FollowError("rule list expected")
    out = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or len(item) > MAX_RULE_LENGTH:
            raise FollowError("invalid rule")
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def rule_key(rule):
    """Forme canonique d'une règle (« Example.com » -> « domain:example.com »)."""
    rule = rule.strip()
    match = re.match(r"^(domain|full|keyword|regexp):(.*)$", rule, re.I)
    if match:
        kind = match.group(1).lower()
        value = match.group(2)
        return kind + ":" + (value if kind == "regexp" else shared.normalize(value))
    return "domain:" + shared.normalize(rule)


# --- différentiel ---------------------------------------------------------
def skip_reason(kind, value, platforms):
    """Raison pour laquelle une nouvelle règle du catalogue n'est pas ajoutée
    automatiquement, ou None. Mêmes critères que les cases décochées par
    défaut à l'import."""
    text = v2fly.rule_text(kind, value)
    if "," in text:
        return "comma"
    if kind in v2fly.UNAPPLIED_KINDS:
        return "unapplied"
    if shared.refused(kind, value, platforms) is not None:
        return "refused"
    if kind in (v2fly.KIND_DOMAIN, v2fly.KIND_FULL):
        name = shared.normalize(value)
        # « tld » (full:com) comme « shared » : exclus du suivi
        if shared.excluded(kind, name, platforms) is not None:
            return "shared"
    return None


def plan_app(items, catalog_rules, domains, declined=(), platforms=frozenset(),
             cap=MAX_APP_RULES):
    """Écarts d'une application suivie.

    items : [(kind, value, attrs)] de la résolution de catalog_refs
    (v2fly.Resolution.items()). Renvoie un dict :
      add, remove           règles ajoutées / retirées de catalog_rules ;
      removed               règles retirées de domains (journal, undo) ;
      domains, catalog_rules  listes complètes après application ;
      skipped               {raison: [règles]} des nouveautés non ajoutées ;
      changed, capped       à appliquer ? plafond dépassé (rien appliqué).
    """
    catalog_rules = list(catalog_rules)
    domains = list(domains)
    resolved = [v2fly.rule_text(k, v) for k, v, _a in items]
    resolved_set = set(resolved)
    imported = set(catalog_rules)
    present = {rule_key(d) for d in domains}
    declined_set = set(declined or ())
    add = []
    skipped = {}
    for kind, value, _attrs in items:
        text = v2fly.rule_text(kind, value)
        if text in imported or text in declined_set or rule_key(text) in present or text in add:
            continue
        reason = skip_reason(kind, value, platforms)
        if reason is not None:
            skipped.setdefault(reason, []).append(text)
            continue
        add.append(text)
    remove = [r for r in catalog_rules if r not in resolved_set]
    remove_set = set(remove)
    domain_set = set(domains)
    removed = [r for r in remove if r in domain_set]
    new_domains = [d for d in domains if d not in remove_set] + add
    new_rules = [r for r in catalog_rules if r not in remove_set] + add
    changed = bool(add or remove)
    capped = changed and len(new_domains) > cap
    return {
        "add": add,
        "remove": remove,
        "removed": removed,
        "domains": new_domains if changed and not capped else domains,
        "catalog_rules": new_rules if changed and not capped else catalog_rules,
        "skipped": skipped,
        "changed": changed and not capped,
        "capped": capped,
        "count": len(new_domains),
    }


def _touched(entry):
    return {rule_key(r) for r in list(entry.get("added") or []) + list(entry.get("removed") or [])}


def plan_undo(entry, domains, catalog_rules, declined, cap=MAX_APP_RULES, journal=()):
    """Inverse d'une entrée du journal pour l'état courant de l'application.

    journal : entrées du journal, des plus anciennes aux plus récentes.
    Renvoie domains, catalog_rules, catalog_declined après annulation, et les
    listes added / removed de l'entrée inverse (journal). FollowError si
    l'entrée est déjà annulée, si c'est elle-même une annulation, si une
    entrée plus récente et non annulée de la même application touche aux
    mêmes règles, ou si le plafond serait dépassé.
    """
    if entry.get("undone_by"):
        raise FollowError("this change was already undone")
    if entry.get("kind") == "undo":
        raise FollowError("cannot undo an undo")
    mine = _touched(entry)
    later = False
    for other in journal or ():
        if other.get("id") == entry.get("id"):
            later = True
            continue
        if (later and other.get("app_uuid") == entry.get("app_uuid") and not other.get("undone_by")
                and mine & _touched(other)):
            raise FollowError("a later change touches the same rules: undo it first")
    added = list(entry.get("added") or [])
    removed = list(entry.get("removed") or [])
    domains = list(domains)
    added_set = set(added)
    removed_set = set(removed)
    before = set(domains)
    new_domains = [d for d in domains if d not in added_set]
    new_rules = [r for r in catalog_rules if r not in added_set]
    present = {rule_key(d) for d in new_domains}
    inverse_added = []
    for rule in removed:
        if rule_key(rule) not in present:
            new_domains.append(rule)       # règle ajoutée à la main : le suivi n'y touche plus
            present.add(rule_key(rule))
            inverse_added.append(rule)
    new_declined = [d for d in declined if d not in removed_set]
    for rule in added:
        if rule not in new_declined:
            new_declined.append(rule)      # le prochain suivi ne la remet pas
    if len(new_domains) > cap:
        raise FollowError("undo would exceed %d rules for this application" % cap)
    return {
        "domains": new_domains,
        "catalog_rules": new_rules,
        "catalog_declined": new_declined,
        "added": inverse_added,
        "removed": [a for a in added if a in before],
    }


# --- journal --------------------------------------------------------------
def _clean_entry(raw):
    if not isinstance(raw, dict):
        return None
    try:
        entry = {
            "id": raw["id"],
            "time": float(raw["time"]),
            "app_uuid": raw["app_uuid"],
            "app": str(raw.get("app") or "")[:64],
            "added": rule_list(raw.get("added")),
            "removed": rule_list(raw.get("removed")),
            "revision": str(raw.get("revision") or "")[:64],
            "kind": raw.get("kind") if raw.get("kind") in KINDS else "follow",
            "undo_of": raw.get("undo_of") if ENTRY_ID_RE.match(str(raw.get("undo_of") or "")) else None,
            "undone_by": raw.get("undone_by") if ENTRY_ID_RE.match(str(raw.get("undone_by") or "")) else None,
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(entry["id"], str) or not ENTRY_ID_RE.match(entry["id"]):
        return None
    if not isinstance(entry["app_uuid"], str) or not UUID_RE.match(entry["app_uuid"]):
        return None
    return entry


def load_journal(path=JOURNAL_PATH):
    """Entrées valides du journal, des plus anciennes aux plus récentes."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        entry = _clean_entry(item)
        if entry is not None:
            out.append(entry)
    return out


def _write_journal(path, entries):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".catalog_follow.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"version": JOURNAL_VERSION, "entries": entries}, fh,
                      separators=(",", ":"), sort_keys=True)
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


class JournalLock:
    """Verrou exclusif bloquant à côté du journal (flock ; sans fcntl : aucun)."""

    def __init__(self, path):
        self.path = path + ".lock"
        self.fd = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        return False


def new_id(existing=()):
    while True:
        value = secrets.token_hex(8)
        if value not in existing:
            return value


def record(entries, path=JOURNAL_PATH, now=None, max_entries=JOURNAL_MAX):
    """Ajoute des entrées (id et date attribués ici) ; une entrée avec
    undo_of marque l'entrée annulée (undone_by). Renvoie les ids."""
    now = time.time() if now is None else now
    if not isinstance(entries, list):
        raise FollowError("entries expected")
    with JournalLock(path):
        journal = load_journal(path)
        ids = {e["id"] for e in journal}
        by_id = {e["id"]: e for e in journal}
        out = []
        for raw in entries:
            if not isinstance(raw, dict):
                raise FollowError("invalid entry")
            entry = _clean_entry(dict(raw, id=new_id(ids), time=now))
            if entry is None:
                raise FollowError("invalid entry")
            ids.add(entry["id"])
            if entry["undo_of"]:
                entry["kind"] = "undo"
                if entry["undo_of"] in by_id:
                    by_id[entry["undo_of"]]["undone_by"] = entry["id"]
            journal.append(entry)
            by_id[entry["id"]] = entry
            out.append(entry["id"])
        _write_journal(path, journal[-max_entries:])
    return out


def find_entry(entry_id, path=JOURNAL_PATH):
    if not isinstance(entry_id, str) or not ENTRY_ID_RE.match(entry_id):
        raise FollowError("invalid entry")
    for entry in load_journal(path):
        if entry["id"] == entry_id:
            return entry
    raise FollowError("entry not found (the journal keeps the last %d changes)" % JOURNAL_MAX)


def recent(path=JOURNAL_PATH, limit=LOG_DEFAULT):
    """Dernières entrées, la plus récente en premier."""
    journal = load_journal(path)
    return {"entries": list(reversed(journal[-limit:])), "total": len(journal), "max": JOURNAL_MAX}
