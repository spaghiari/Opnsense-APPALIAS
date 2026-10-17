"""Correspondance entre noms DNS et règles des applications.

Règles (MVP) :
- « domain:x » ou « x » seul : x et tous ses sous-domaines ;
- « full:x » : x exactement ;
- « keyword:… » et « regexp:… » : acceptés mais ignorés (phase 3),
  avec un avertissement dans le journal.
"""

import logging

from .dnswire import normalize_name

KIND_DOMAIN = "domain"
KIND_FULL = "full"
IGNORED_KINDS = ("keyword", "regexp")
KNOWN_KINDS = (KIND_DOMAIN, KIND_FULL) + IGNORED_KINDS

log = logging.getLogger("appalias")


def parse_rule(rule):
    """Renvoie (type, valeur) ou None si la règle est vide ou inconnue."""
    if not isinstance(rule, str):
        return None
    rule = rule.strip()
    if not rule:
        return None
    kind = KIND_DOMAIN
    value = rule
    if ":" in rule:
        prefix, rest = rule.split(":", 1)
        prefix = prefix.strip().lower()
        if prefix not in KNOWN_KINDS:
            return ("unknown", rule)
        kind = prefix
        value = rest.strip()
    if kind in (KIND_DOMAIN, KIND_FULL):
        value = normalize_name(value)
        if value.startswith("*."):
            value = value[2:]
        value = value.lstrip(".")
        if not value:
            return None
    elif not value:
        return None
    return (kind, value)


class Matcher:
    """Index des règles : nom exact et suffixes de domaine."""

    def __init__(self, rules_by_table, logger=None):
        self.log = logger or log
        self._full = {}
        self._domain = {}
        self.ignored = []
        for table, rules in rules_by_table.items():
            for rule in rules or ():
                parsed = parse_rule(rule)
                if parsed is None:
                    continue
                kind, value = parsed
                if kind == KIND_FULL:
                    self._full.setdefault(value, set()).add(table)
                elif kind == KIND_DOMAIN:
                    self._domain.setdefault(value, set()).add(table)
                elif kind in IGNORED_KINDS:
                    self.ignored.append((table, rule))
                    self.log.warning(
                        "règle « %s » de %s ignorée : %s: n'est pas pris en charge "
                        "dans cette version (prévu en phase 3)", rule, table, kind)
                else:
                    self.ignored.append((table, rule))
                    self.log.warning("règle « %s » de %s ignorée : préfixe inconnu",
                                     rule, table)

    def match_name(self, name):
        """Tables concernées par un nom."""
        name = normalize_name(name)
        if not name:
            return set()
        found = set(self._full.get(name, ()))
        labels = name.split(".")
        for i in range(len(labels)):
            found.update(self._domain.get(".".join(labels[i:]), ()))
        return found

    def match(self, names):
        """Tables concernées par l'un des noms."""
        found = set()
        for name in names:
            found.update(self.match_name(name))
        return found

    def __bool__(self):
        return bool(self._full or self._domain)
