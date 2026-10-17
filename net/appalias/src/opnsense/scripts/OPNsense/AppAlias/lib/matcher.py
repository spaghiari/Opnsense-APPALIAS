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

"""Correspondance entre noms DNS et règles des applications.

Règles :
- « domain:x » ou « x » seul : x et tous ses sous-domaines ;
- « full:x » : x exactement ;
- « keyword:… » et « regexp:… » : règles de découverte, conservées dans la
  configuration mais jamais appliquées par le relais (une seule ligne de
  journal, en debug, avec leur nombre).

Garde-fou (lib/shared.py) : « domain:x » est refusé quand x est un domaine de
premier niveau seul, ou égal ou parent d'une plateforme partagée (une seule
ligne de journal pour l'ensemble des refus).
"""

import logging

from . import shared
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

    def __init__(self, rules_by_table, logger=None, platforms=None):
        self.log = logger or log
        self.platforms = shared.load() if platforms is None else frozenset(platforms)
        self._full = {}
        self._domain = {}
        self.discovery = []
        self.refused = []
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
                    if shared.refused(kind, value, self.platforms):
                        self.refused.append((table, rule))
                    else:
                        self._domain.setdefault(value, set()).add(table)
                elif kind in IGNORED_KINDS:
                    self.discovery.append((table, rule))
                    self.ignored.append((table, rule))
                else:
                    self.ignored.append((table, rule))
                    self.log.warning("règle « %s » de %s ignorée : préfixe inconnu",
                                     rule, table)
        if self.refused:
            self.log.warning(
                "%d règle(s) domain: refusée(s) (domaine de premier niveau, ou plateforme "
                "partagée ou parent de l'une d'elles ; utiliser full:) : %s",
                len(self.refused),
                ", ".join("%s (%s)" % (r, t) for t, r in self.refused[:5])
                + (", …" if len(self.refused) > 5 else ""))
        if self.discovery:
            self.log.debug(
                "%d règle(s) de découverte (keyword:, regexp:) conservée(s), "
                "non appliquée(s) par le relais", len(self.discovery))

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
