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

"""Propositions de la découverte : score et explications (hors démon).

Entrées : discovery.json (lib/discovery.py), la configuration (lib/config.py)
et le catalogue v2fly (règles résolues des listes importées, index inverse
des domaines). Sortie : propositions par application, noms non attribués
groupés par eTLD+1. Rien n'est appliqué ici.

Barème, par (nom non couvert, application A), plafonné à 100 :

- cname_neighbor 60 : le nom a la même cible CNAME exacte qu'un nom couvert
  par A (cible hors plateforme partagée) ;
- chain 50 : une chaîne CNAME du nom passe par un nom couvert par A ;
- v2fly_imported 40 : le nom est dans une liste v2fly déjà importée dans A
  (règle non retenue à l'import) ;
- cooccurrence jusqu'à 40 : vu dans la fenêtre ±W des passages de A, au
  moins MIN_COOC_VIEWS vues et lift >= MIN_LIFT ;
- same_site 30 : même eTLD+1 qu'une règle domain:/full: de A ;
- keyword / regexp 35 : une règle keyword: ou regexp: de A correspond.

Affiché à partir de SHOW_MIN, « strong » à partir de STRONG_MIN.
Explications : codes structurés ({"code": "chain", "name": ...}), traduits
par l'interface.

Règle proposée : full:<nom> ; domain:<eTLD+1> quand au moins GROUP_MIN noms
de la même application partagent cet eTLD+1, hors plateforme partagée. Un nom
qui touche une plateforme partagée reste en full:, décoché.

eTLD+1 : liste des suffixes publics du système si elle est présente
(PSL_PATHS), sinon heuristique (deux labels, trois pour « co.uk » et
semblables). regexp: : 256 caractères au plus, quantificateurs imbriqués
refusés, temps total borné par signal.alarm (REGEX_BUDGET secondes).
"""

import re
import signal
import time

from . import shared
from .matcher import Matcher, parse_rule

SHOW_MIN = 40
STRONG_MIN = 75
MAX_SCORE = 100
GROUP_MIN = 3
MIN_COOC_VIEWS = 5
MIN_LIFT = 3.0
MAX_PROPOSALS = 2000
MAX_UNASSIGNED = 1000
MAX_V2FLY_LISTS = 4
MAX_REGEX = 256
REGEX_BUDGET = 10

POINTS = {
    "cname_neighbor": 60,
    "chain": 50,
    "v2fly_imported": 40,
    "cooccurrence": 40,     # plafond
    "same_site": 30,
    "keyword": 35,
    "regexp": 35,
}

PSL_PATHS = (
    "/usr/local/share/public_suffix_list/public_suffix_list.dat",
    "/usr/share/publicsuffix/public_suffix_list.dat",
)

# Heuristique sans liste : deuxième niveau générique sous un ccTLD.
SECOND_LEVELS = frozenset(("ac", "co", "com", "edu", "gov", "go", "ltd", "me", "mil", "ne",
                           "net", "nic", "or", "org", "plc", "sch"))

NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


# --- eTLD+1 ---------------------------------------------------------------
class Suffixes:
    """Liste des suffixes publics (format publicsuffix.org) ou heuristique."""

    def __init__(self, text=None):
        self.rules = set()
        self.wild = set()
        self.exceptions = set()
        if text:
            for raw in text.splitlines():
                line = raw.strip().split(" ", 1)[0]
                if not line or line.startswith("//"):
                    continue
                line = line.lower()
                if line.startswith("!"):
                    self.exceptions.add(line[1:])
                elif line.startswith("*."):
                    self.wild.add(line[2:])
                else:
                    self.rules.add(line)

    @classmethod
    def load(cls, paths=PSL_PATHS):
        for path in paths:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return cls(fh.read())
            except OSError:
                continue
        return cls()

    def __bool__(self):
        return bool(self.rules or self.wild)

    def suffix_len(self, labels):
        """Nombre de labels du suffixe public de labels."""
        if not self:
            if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVELS:
                return 2
            return 1
        best = 1
        for i in range(len(labels)):
            cand = ".".join(labels[i:])
            n = len(labels) - i
            if cand in self.exceptions:
                return n - 1
            if cand in self.rules and n > best:
                best = n
            if i + 1 < len(labels) and ".".join(labels[i + 1:]) in self.wild and n > best:
                best = n
        return best

    def site(self, name):
        """eTLD+1 de name (name lui-même s'il est un suffixe public)."""
        labels = [l for l in str(name).lower().rstrip(".").split(".") if l]
        if not labels:
            return ""
        n = self.suffix_len(labels)
        return ".".join(labels[-(n + 1):]) if len(labels) > n else ".".join(labels)


# --- regexp: sûres ----------------------------------------------------------
def safe_regex(pattern):
    """Expression compilée, ou None si trop longue, imbriquée ou invalide."""
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX:
        return None
    if NESTED_QUANTIFIER.search(pattern):
        return None
    try:
        return re.compile(pattern)
    except (re.error, RecursionError, OverflowError):
        return None


class _Budget(Exception):
    pass


class RegexBudget:
    """Temps total borné pour les recherches regexp: (REGEX_BUDGET secondes).

    L'alarme (signal.alarm, si disponible) n'interrompt que les recherches
    faites par search() : expirée ailleurs, elle marque seulement le budget
    épuisé. Une fois épuisé, plus aucune regexp n'est évaluée et la notation
    continue sans elles (regex_timeout dans le résultat). Sans SIGALRM, une
    échéance en temps monotone est vérifiée avant chaque recherche.
    """

    def __init__(self, seconds=REGEX_BUDGET):
        self.seconds = seconds
        self.exhausted = False
        self.active = False
        self.deadline = None
        self._old = None

    def _alarm(self, _signum, _frame):
        self.exhausted = True
        if self.active:
            raise _Budget()

    def __enter__(self):
        self.deadline = time.monotonic() + self.seconds
        if hasattr(signal, "SIGALRM"):
            self._old = signal.signal(signal.SIGALRM, self._alarm)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self._old or signal.SIG_DFL)
        return False

    def search(self, compiled, text):
        """Vrai si compiled trouve text ; faux (et budget épuisé) à l'échéance."""
        if self.exhausted or (self.deadline is not None and time.monotonic() > self.deadline):
            self.exhausted = True
            return False
        try:
            self.active = True
            return compiled.search(text) is not None
        except _Budget:
            self.exhausted = True
            return False
        finally:
            self.active = False


# --- applications ------------------------------------------------------------
class AppRules:
    """Règles d'une application, préparées pour le score."""

    def __init__(self, app, platforms, suffixes, catalog_rules=None):
        self.uuid = app.uuid
        self.name = app.name
        self.table = app.table
        self.enabled = app.enabled
        self.refs = list(app.catalog_refs)
        self.sites = set()
        self.keywords = []
        self.regexps = []
        for rule in app.domains:
            parsed = parse_rule(rule)
            if parsed is None:
                continue
            kind, value = parsed
            if kind in ("domain", "full"):
                self.sites.add(suffixes.site(value))
            elif kind == "keyword":
                self.keywords.append(value.lower())
            elif kind == "regexp":
                compiled = safe_regex(value)
                if compiled is not None:
                    self.regexps.append((value, compiled))
        # Règles résolues de chaque liste importée : (ref, Matcher).
        self.lists = []
        for ref, rules in (catalog_rules or {}).items():
            self.lists.append((ref, Matcher({self.table: rules}, platforms=platforms,
                                            logger=_Silent())))

    @property
    def base_lists(self):
        return {ref.split("@", 1)[0] for ref in self.refs}


class _Silent:
    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


# --- score -----------------------------------------------------------------
def _cooc(stat, app, data):
    views = stat["views"]
    count = stat["cooc"].get(app.table, 0)
    total = data.get("windows_total") or 0
    active = (data.get("apps") or {}).get(app.table, {}).get("active", 0)
    if views < MIN_COOC_VIEWS or count <= 0 or total <= 0 or active <= 0:
        return None
    base = min(1.0, active / float(total))
    lift = (count / float(views)) / base
    if lift < MIN_LIFT:
        return None
    points = min(POINTS["cooccurrence"], int(round(8 * lift)))
    return points, {"code": "cooccurrence", "count": count, "views": views, "lift": round(lift, 1)}


def score_name(name, stat, app, data, platforms, suffixes, budget):
    """(score, raisons) pour un nom non couvert et une application."""
    reasons = []
    total = 0
    targets = (data.get("targets") or {})
    for target in stat["targets"]:
        if shared.touches(target, platforms):
            continue
        entry = targets.get(target) or {}
        hit = [n for t, n in entry.get("names", []) if t == app.table and n != name]
        if hit:
            total += POINTS["cname_neighbor"]
            reasons.append({"code": "cname_neighbor", "target": target, "name": hit[0]})
            break
    chain = [n for t, n in stat["links"] if t == app.table]
    if chain:
        total += POINTS["chain"]
        reasons.append({"code": "chain", "name": chain[0]})
    for ref, matcher in app.lists:
        if matcher.match_name(name):
            total += POINTS["v2fly_imported"]
            reasons.append({"code": "v2fly_imported", "list": ref})
            break
    cooc = _cooc(stat, app, data)
    if cooc is not None:
        total += cooc[0]
        reasons.append(cooc[1])
    site = suffixes.site(name)
    if site in app.sites:
        total += POINTS["same_site"]
        reasons.append({"code": "same_site", "site": site})
    matched = None
    for kw in app.keywords:
        if kw in name:
            matched = {"code": "keyword", "rule": "keyword:" + kw}
            break
    if matched is None and app.regexps and not budget.exhausted:
        for text, compiled in app.regexps:
            if budget.exhausted:
                break
            if budget.search(compiled, name):
                matched = {"code": "regexp", "rule": "regexp:" + text}
                break
    if matched is not None:
        total += POINTS[matched["code"]]
        reasons.append(matched)
    return min(MAX_SCORE, total), reasons


def v2fly_lists(name, domains_index, imported):
    """Listes v2fly (non importées) qui contiennent une règle sur ce nom."""
    labels = name.split(".")
    keys = ["full:" + name] + ["domain:" + ".".join(labels[i:]) for i in range(len(labels) - 1)]
    out = []
    for key in keys:
        for list_name in sorted(domains_index.get(key, ())):
            if list_name not in imported and list_name not in out:
                out.append(list_name)
    return out[:MAX_V2FLY_LISTS]


def build(data, apps, platforms, suffixes, domains_index=None, ignored=None, now=None):
    """Propositions et noms non attribués à partir de discovery.json.

    apps : [AppRules] ; ignored : objet avec match(nom) (lib.discovery.Ignored).
    """
    domains_index = domains_index or {}
    names = data.get("names") or {}
    learn = data.get("learn") or {}
    learn_names = learn.get("names") or {}
    imported = set()
    for app in apps:
        imported.update(app.base_lists)
    proposals = []
    unassigned = []
    with RegexBudget() as budget:
        for name in sorted(names):
            stat = names[name]
            if ignored is not None and ignored.match(name):
                continue
            seen = learn_names.get(name)
            common = {
                "name": name,
                "views": stat["views"],
                "first": stat["first"],
                "last": stat["last"],
                "shared": shared.touches(name, platforms),
                "learn": seen is not None,
                "new": bool(seen and seen.get("new")),
                "site": suffixes.site(name),
            }
            best = 0
            for app in apps:
                points, reasons = score_name(name, stat, app, data, platforms, suffixes, budget)
                best = max(best, points)
                if points < SHOW_MIN:
                    continue
                item = dict(common, app_uuid=app.uuid, app=app.name, score=points,
                            strong=points >= STRONG_MIN, reasons=reasons)
                proposals.append(item)
            if best < SHOW_MIN:
                item = dict(common, v2fly=v2fly_lists(name, domains_index, imported))
                unassigned.append(item)
        regex_timeout = budget.exhausted
    _choose_rules(proposals, platforms)
    proposals.sort(key=lambda p: (p["app"], -p["score"], p["name"]))
    unassigned.sort(key=lambda u: (-u["views"], u["name"]))
    return {
        "proposals": proposals[:MAX_PROPOSALS],
        "unassigned": _group_unassigned(unassigned[:MAX_UNASSIGNED]),
        "regex_timeout": regex_timeout,
    }


def _choose_rules(proposals, platforms):
    """Règle par défaut et options (full:, domain:<eTLD+1> si groupe)."""
    groups = {}
    for item in proposals:
        groups.setdefault((item["app_uuid"], item["site"]), []).append(item)
    for (_uuid, site), items in groups.items():
        site_ok = (len(items) >= GROUP_MIN and "." in site
                   and shared.refused("domain", site, platforms) is None
                   and not shared.touches(site, platforms))
        for item in items:
            full = "full:" + item["name"]
            item["options"] = [full]
            item["rule"] = full
            item["group"] = None
            item["checked"] = not item["shared"]
            if item["shared"]:
                continue
            if site_ok:
                item["options"].append("domain:" + site)
                item["rule"] = "domain:" + site
                item["group"] = site


def _group_unassigned(items):
    groups = {}
    order = []
    for item in items:
        site = item["site"]
        if site not in groups:
            groups[site] = []
            order.append(site)
        groups[site].append(item)
    return [{"site": site, "views": sum(i["views"] for i in groups[site]), "names": groups[site]}
            for site in order]
