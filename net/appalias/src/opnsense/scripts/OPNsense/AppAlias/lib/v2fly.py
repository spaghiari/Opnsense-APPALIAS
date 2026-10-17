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

"""Analyseur du catalogue v2fly embarqué (catalog/v2fly.lst).

Format du .lst (produit par tools/v2fly-snapshot.py) : une section
« [liste] » par liste, puis une règle par ligne, au format v2fly :

- « domain:x » ou « x » seul : x et ses sous-domaines ;
- « full:x » : x exactement ;
- « keyword:x », « regexp:x » : règles de découverte (conservées, jamais
  appliquées par le relais) ;
- « include:liste » : règles d'une autre liste, filtrables par attribut
  (« @ads » : seulement les règles qui portent @ads ; « @-ads » : toutes
  sauf celles-là) ;
- attributs « @x » après la règle.

Une ligne inconnue ou non ASCII est comptée et signalée, jamais fatale.
Résolution des include: par parcours en profondeur à trois états, avec
mémoïsation et profondeur maximale MAX_DEPTH ; un cycle rend la liste
« cassée » (chemin « a > b > a ») et non importable ; un include manquant est
signalé. Doublons retirés, noms normalisés (minuscules, point final retiré).

Référence (« ref ») : nom de liste suivi de filtres, « netflix@-ads ».
"""

import re

KIND_DOMAIN = "domain"
KIND_FULL = "full"
KIND_KEYWORD = "keyword"
KIND_REGEXP = "regexp"
KINDS = (KIND_DOMAIN, KIND_FULL, KIND_KEYWORD, KIND_REGEXP)
DISCOVERY_KINDS = (KIND_KEYWORD, KIND_REGEXP)
KIND_ORDER = {k: i for i, k in enumerate(KINDS)}

MAX_DEPTH = 32
MAX_REPORTED = 50

REF_RE = re.compile(r"^[a-z0-9-]+(@-?[a-z0-9-]+)*$")
SECTION_RE = re.compile(r"^\[([^\]]*)\]$")
LIST_NAME_RE = re.compile(r"^[a-z0-9!._-]{1,128}$")
ATTR_RE = re.compile(r"^[a-z0-9!_-]{1,64}$")
HOST_RE = re.compile(r"^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$")
KEYWORD_RE = re.compile(r"^[a-z0-9._-]{1,253}$")

WHITE, GRAY, BLACK = 0, 1, 2


class RefError(ValueError):
    """Référence de liste invalide."""


def rule_text(kind, value):
    return "%s:%s" % (kind, value)


def sort_key(item):
    kind, value = item
    return (KIND_ORDER.get(kind, 99), value)


def parse_filters(tokens):
    """« @a », « @-b » -> tuple de (positif, attribut) ; None si invalide."""
    out = []
    for token in tokens:
        if not token.startswith("@"):
            return None
        attr = token[1:]
        positive = True
        if attr.startswith("-"):
            positive = False
            attr = attr[1:]
        attr = attr.lower()
        if not ATTR_RE.match(attr):
            return None
        out.append((positive, attr))
    return tuple(out)


def keep(attrs, filters):
    """Vrai si une règle d'attributs attrs passe tous les filtres."""
    for positive, attr in filters:
        if positive != (attr in attrs):
            return False
    return True


def parse_ref(ref):
    """« netflix@-ads » -> ("netflix", ((False, "ads"),)). RefError si invalide."""
    if not isinstance(ref, str) or len(ref) > 128 or not REF_RE.match(ref):
        raise RefError("référence invalide")
    parts = ref.split("@")
    filters = parse_filters(["@" + p for p in parts[1:]])
    if filters is None:
        raise RefError("référence invalide")
    return parts[0], filters


def parse_rule_line(line):
    """Ligne -> ("rule", kind, value, attrs) ou ("include", nom, filtres) ;
    None si la ligne est inconnue, invalide ou non ASCII."""
    if not line.isascii():
        return None
    tokens = line.split()
    if not tokens:
        return None
    head, rest = tokens[0], tokens[1:]
    if ":" in head:
        prefix, value = head.split(":", 1)
        prefix = prefix.lower()
    else:
        prefix, value = KIND_DOMAIN, head
    if prefix == "include":
        name = value.lower()
        filters = parse_filters(rest)
        if not LIST_NAME_RE.match(name) or filters is None:
            return None
        return ("include", name, filters)
    if prefix not in KINDS:
        return None
    attrs = set()
    for token in rest:
        attr = token[1:].lower()
        if not token.startswith("@") or not ATTR_RE.match(attr):
            return None
        attrs.add(attr)
    if prefix in (KIND_DOMAIN, KIND_FULL):
        value = value.lower().rstrip(".")
        if len(value) > 253 or not HOST_RE.match(value):
            return None
    elif prefix == KIND_KEYWORD:
        value = value.lower()
        if not KEYWORD_RE.match(value):
            return None
    elif not value or len(value) > 1024:
        return None
    return ("rule", prefix, value, frozenset(attrs))


class ListDef:
    """Contenu propre d'une liste (avant résolution des include:)."""

    __slots__ = ("name", "rules", "includes", "unknown")

    def __init__(self, name):
        self.name = name
        self.rules = {}       # (kind, value) -> frozenset(attrs)
        self.includes = []    # (nom, filtres)
        self.unknown = []     # (numéro de ligne, texte)


class Resolution:
    """Résultat de la résolution d'une liste ou d'une référence."""

    __slots__ = ("name", "rules", "missing", "broken", "unknown", "chain")

    def __init__(self, name):
        self.name = name
        self.rules = {}        # (kind, value) -> frozenset(attrs)
        self.missing = set()   # include: vers une liste absente
        self.broken = None     # chemin du cycle ou de la profondeur dépassée
        self.unknown = 0       # lignes inconnues (liste et listes incluses)
        self.chain = (name,)   # plus longue chaîne d'include: depuis cette liste

    @property
    def importable(self):
        return self.broken is None

    def items(self):
        """[(kind, value, attrs)] triés (domain, full, keyword, regexp)."""
        return [(k, v, self.rules[(k, v)]) for k, v in sorted(self.rules, key=sort_key)]

    def texts(self):
        return [rule_text(k, v) for k, v, _a in self.items()]

    def attrs(self):
        out = set()
        for attrs in self.rules.values():
            out.update(attrs)
        return out


class Catalog:
    """Catalogue analysé ; resolve() mémoïse les listes résolues."""

    def __init__(self):
        self.lists = {}
        self.problems = []   # (numéro de ligne, texte, raison) hors règles
        self._memo = {}

    @classmethod
    def parse(cls, text):
        cat = cls()
        current = None
        for lineno, raw in enumerate(str(text).splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            shown = line if line.isascii() else ascii(line)
            match = SECTION_RE.match(line)
            if match:
                name = match.group(1).strip().lower()
                if not line.isascii() or not LIST_NAME_RE.match(name):
                    current = None
                    cat._problem(lineno, shown, "nom de liste invalide")
                    continue
                current = cat.lists.get(name)
                if current is None:
                    current = cat.lists[name] = ListDef(name)
                else:
                    cat._problem(lineno, shown, "section en double (fusionnée)")
                continue
            if current is None:
                cat._problem(lineno, shown, "ligne hors section")
                continue
            parsed = parse_rule_line(line)
            if parsed is None:
                current.unknown.append((lineno, shown))
            elif parsed[0] == "include":
                current.includes.append((parsed[1], parsed[2]))
            else:
                _tag, kind, value, attrs = parsed
                key = (kind, value)
                current.rules[key] = current.rules.get(key, frozenset()) | attrs
        return cat

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return cls.parse(fh.read())

    def _problem(self, lineno, line, reason):
        if len(self.problems) < MAX_REPORTED:
            self.problems.append((lineno, line, reason))

    def names(self):
        return sorted(self.lists)

    def unknown_lines(self):
        """[(liste, numéro de ligne, texte)] des lignes inconnues."""
        out = []
        for name in self.names():
            for lineno, line in self.lists[name].unknown:
                out.append((name, lineno, line))
        return out

    # -- résolution --
    def resolve(self, name):
        """Résolution complète (sans filtre) d'une liste ; KeyError si absente."""
        name = name.lower()
        if name not in self.lists:
            raise KeyError(name)
        res, _tainted = self._visit(name, [], {})
        return res

    def _visit(self, name, stack, state):
        """Parcours en profondeur à trois états. Renvoie (Resolution, tainted).

        La profondeur d'une liste est la longueur de sa plus longue chaîne
        d'include: (propriété de la liste, donc mémoïsable). La pile est aussi
        bornée pour limiter la récursion : une cassure vue seulement à cause
        de la pile (tainted) dépend du point de départ et n'est pas mémoïsée."""
        memo = self._memo.get(name)
        if memo is not None:
            return memo, False
        if state.get(name, WHITE) == GRAY:
            cycle = stack[stack.index(name):] + [name]
            res = Resolution(name)
            res.broken = "cycle: " + " > ".join(cycle)
            return res, False
        if len(stack) >= MAX_DEPTH:
            res = Resolution(name)
            res.broken = "depth > %d: %s" % (MAX_DEPTH, " > ".join(stack + [name]))
            res.chain = tuple(stack[-MAX_DEPTH:]) + (name,)
            return res, True
        state[name] = GRAY
        stack.append(name)
        ldef = self.lists[name]
        res = Resolution(name)
        tainted = False
        res.rules = dict(ldef.rules)
        res.unknown = len(ldef.unknown)
        for inc, filters in ldef.includes:
            if inc not in self.lists:
                res.missing.add(inc)
                continue
            sub, sub_tainted = self._visit(inc, stack, state)
            tainted = tainted or sub_tainted
            if sub.broken is not None and res.broken is None:
                res.broken = sub.broken
            res.missing.update(sub.missing)
            res.unknown += sub.unknown
            if len(sub.chain) + 1 > len(res.chain):
                res.chain = (name,) + sub.chain[:MAX_DEPTH]
            for key, attrs in sub.rules.items():
                if keep(attrs, filters):
                    res.rules[key] = res.rules.get(key, frozenset()) | attrs
        stack.pop()
        state[name] = BLACK
        if res.broken is None and len(res.chain) > MAX_DEPTH:
            res.broken = "depth > %d: %s" % (MAX_DEPTH, " > ".join(res.chain))
        if res.broken is not None:
            res.rules = {}
        if not tainted:
            self._memo[name] = res
        return res, tainted

    def resolve_ref(self, ref):
        """Résolution d'une référence « liste@filtre… ». RefError, KeyError."""
        name, filters = parse_ref(ref)
        base = self.resolve(name)
        res = Resolution(ref)
        res.missing = set(base.missing)
        res.broken = base.broken
        res.unknown = base.unknown
        res.rules = {k: a for k, a in base.rules.items() if keep(a, filters)}
        return res

    def resolve_refs(self, refs):
        """Union de plusieurs références (diff d'une application)."""
        out = Resolution(",".join(refs))
        for ref in refs:
            res = self.resolve_ref(ref)
            out.missing.update(res.missing)
            out.unknown += res.unknown
            if res.broken is not None and out.broken is None:
                out.broken = res.broken
            for key, attrs in res.rules.items():
                out.rules[key] = out.rules.get(key, frozenset()) | attrs
        if out.broken is not None:
            out.rules = {}
        return out
